"""The /btw answer box of Claude Code 2.1.316 as two installations draw it (screens/2.1.316/extra/T, see the README
there): under a row of "▔" (edge), and without one, the question standing in the conversation above (inline: the
bot of v0.5.0 never saw that box, so the answer was never posted and the box was left open; #discord-control
2026-11-03). Both are read; what only looks like the inline box is not."""
import re
import unittest

from altention.connectors.claude_code import screen

from . import samples

V = "2.1.316"
ANSWER = "#試用 的工作目錄是 /hdir/user-x/altention/engine，也就是引擎資料夾。"
QUESTIONS = ["/btw 剛剛建了哪兩個檔案？", "/btw （這是新的旁支問題，和先前其他 /btw 的問答無關）這個頻道的工作目錄是哪裡？"]


def T(state, esc=False):
    return samples.extra("T", state, esc=esc, version=V)


class Samples(unittest.TestCase):
    def test_they_are_whole_and_say_their_version(self):
        states = samples.extra_states("T", version=V)
        self.assertEqual(sorted(states), sorted(["inline-answering", "inline-answer", "edge-answer-first", "edge-answer-second",
                                                 "edge-answer-second-60x24", "edge-answer-tall", "edge-answer-busy"]))
        for state in states:
            meta = samples.extra_meta("T", state, version=V)
            width, height = meta["pane"]
            rows = T(state).split("\n")
            self.assertTrue(meta["claude_code"].startswith(V) and meta["how"], state)
            self.assertEqual(len(rows), height + 1, state)
            flat = re.sub(r"\x1b\[[0-9;:]*m|\s", "", T(state) + T(state, esc=True))
            self.assertFalse(re.search(r"/home/|1566\d{15}", flat), (state, "made-up names and IDs only"))

    def test_the_box_drawn_inline_is_read(self):
        view = T("inline-answer")
        questions, _, hint = screen.btw_box(view)
        self.assertEqual([q.strip() for q in questions], QUESTIONS)
        self.assertEqual(hint.strip(), "⇧←/→ to browse · c to copy · f to fork · x to clear history · Esc to close")
        self.assertEqual(screen.btw_answer(view), ANSWER)
        self.assertEqual(screen.pane_state(view), "dialog")

    def test_on_its_way_inline(self):
        view = T("inline-answering")
        self.assertEqual(screen.btw_answer(view), "", "the box is there, nothing to copy yet")
        self.assertEqual(screen.btw_hint(view).strip(), "⇧←/→ to browse · x to clear history · Esc to close")

    def test_the_box_under_its_edge_as_before(self):
        for state, start in (("edge-answer-first", "The side answer is forty-two."), ("edge-answer-second", "The side answer is forty-two."),
                             ("edge-answer-second-60x24", "The side answer is forty-two."), ("edge-answer-busy", "The side answer is forty-two."),
                             ("edge-answer-tall", "Line 1 of the side answer")):
            view = T(state)
            self.assertTrue(screen.btw_answer(view, samples.extra_meta("T", state, version=V)["pane"][0]).startswith(start), state)
            self.assertTrue(screen.btw_hint(view).endswith("Esc to close"), state)
            self.assertEqual("x to clear history" in screen.btw_hint(view), "second" in state, state)

    def rows(self):
        return T("inline-answer").split("\n")

    def at(self, text):
        return self.rows().index(next(r for r in self.rows() if r.startswith(text)))

    def test_the_question_sent_wrapped_onto_more_rows(self):
        rows = self.rows()
        i = self.at("❯ /btw")
        rows[i:i + 1] = [rows[i][:40], "  " + rows[i][40:]]
        self.assertEqual(screen.btw_answer("\n".join(rows[1:])), ANSWER)

    def test_a_question_that_wraps_onto_two_rows(self):
        """#code-review on ec76319: a long question (from Discord they often are) wrapped in the box is still read;
        not measured on the real screen (120 columns did not wrap it), so made from it."""
        rows = self.rows()
        q = self.at("  /btw （")
        rows[q:q + 1] = [rows[q][:30], "  " + rows[q][30:].lstrip()]
        questions, _, _ = screen.btw_box("\n".join(rows[:-1]))
        self.assertEqual(len(questions), 3)
        self.assertEqual(screen.btw_answer("\n".join(rows[:-1])), ANSWER)
        first = self.at("  /btw 剛剛")
        rows[first:first + 1] = ["  剛剛建了哪兩個檔案？"]  # the top question row not starting "/btw": not the box
        self.assertIsNone(screen.btw_box("\n".join(rows[:-1])))

    def test_a_tall_answer_that_pushed_the_conversation_off(self):
        rows = self.rows()
        q = self.at("  /btw 剛剛")
        self.assertEqual(screen.btw_answer("\n".join(rows[q:] + [""] * q)), ANSWER)
        self.assertEqual(screen.btw_answer("\n".join(rows[q - 1:])), ANSWER, "only an empty row above the questions")

    def test_what_only_looks_like_it_is_not_the_box(self):
        rows = self.rows()
        m, q, a, h = self.at("❯ /btw"), self.at("  /btw 剛剛"), self.at("    #試用"), self.at("  ⇧←/→")
        cases = {
            "a message of the owner's that is no /btw over it": rows[:m] + ["❯ " + rows[m][6:]] + rows[m + 1:],
            "something right over the questions": rows[:q - 1] + ["  ⎿  done"] + rows[q:],
            "a question row that is not /btw": rows[:q] + ["  btw 剛剛建了哪兩個檔案？"] + rows[q + 1:],
            "no empty row between the questions and the answer": rows[:a - 1] + rows[a:] + [""],
            "the hint not the last of the screen": rows[:h + 1] + ["  more words right under it"] + rows[h + 1:],
            "more under it, after an empty row": rows[:h + 1] + ["", "  ⎿  more words"] + rows[h + 1:],
            "a hint at no indent": rows[:h] + [rows[h].lstrip()] + rows[h + 1:],
            "the answer at the questions' indent": rows[:a] + [rows[a][2:]] + rows[a + 1:],
        }
        for what, view in cases.items():
            self.assertIsNone(screen.btw_box("\n".join(view)), what)
            self.assertIsNone(screen.btw_answer("\n".join(view)), what)

    def test_a_conversation_that_quotes_the_hint_is_not_the_box(self):
        quoted = "\n".join(["● The box ends with:", "", "  /btw what?", "", "    an answer", "",
                            "  ⇧←/→ to browse · c to copy · f to fork · Esc to close", "", "─" * 120, "❯ ", "─" * 120,
                            "  ⏵⏵ auto mode on (shift+tab to cycle)", ""])
        self.assertIsNone(screen.btw_box(quoted), "the input box is there")


if __name__ == "__main__":
    unittest.main()
