"""What the functions that read the screen say of real screens (screens/2.1.296, captured whole by
#code-review-2 on 2026-10-11): the same in colour and without, at the bot's size and at 80x24.

Until now those functions were tested on screens put together by hand: what their author thought Claude Code draws.
Stage 3b moves them; these say what must still come out, on the real thing. A new Claude Code gets a new folder
beside this one and a run of the same table."""
import os
import re
import unittest

from altention.connectors.claude_code import screen

from . import samples

BOX = ("idle-fresh-auto", "idle-fresh", "idle-typed", "busy", "busy-queued", "busy-queued-typed", "idle-after-turns",
       "permission-refused", "slash-typed", "btw-closed", "idle-bypass", "idle-suggestion")  # the input box has the keyboard
IN_BOX = {"idle-typed": "half a line typed, not sent", "busy-queued-typed": "more typed", "slash-typed": "/model",
          "idle-suggestion": "run the tests"}
ANSWERED = ("btw-answer", "btw-answer-second", "btw-answer-busy", "btw-answer-cleared")


class Samples(unittest.TestCase):
    def test_all_of_them_are_there_and_say_their_version(self):
        for version in samples.VERSIONS:
            for which in samples.SETS:
                width, height = (int(n) for n in which.split("-")[1].split("x"))
                for state in samples.STATES:
                    at = (version, which, state)
                    meta = samples.meta(state, which, version=version)
                    self.assertTrue(meta["claude_code"].startswith(version), at)
                    self.assertEqual(meta["pane"], [width, height], at)
                    rows = samples.screen(state, which, version=version).split("\n")
                    self.assertEqual(len(rows), height + 1, "the whole pane, every row (and the newline after the last)")
                    self.assertLessEqual(max(len(r) for r in rows), width, at)
                    if which.startswith("colour"):
                        self.assertIn("\x1b[", samples.screen(state, which, esc=True, version=version), at)
                    self.assertNotIn("\x1b", samples.screen(state, which, version=version), at)

    def test_nothing_of_the_machine_they_were_taken_on(self):
        """Also where a narrow pane breaks the path in the middle of a word: looked for with every row break,
        colour and space taken out."""
        seen = set()
        for version in samples.VERSIONS:
            for top, _dirs, files in os.walk(os.path.join(samples.HERE, version)):
                for name in files:
                    with open(os.path.join(top, name), encoding="utf-8") as f:
                        flat = re.sub(r"\x1b\[[0-9;:]*m|\s", "", f.read())
                    seen.update(re.findall(r"/tmp/claude-\d+/-[a-z0-9-]+?-agent-home", flat))
        self.assertEqual(seen, {"/tmp/claude-1000001/-srv-abc-home-someone123-agent-home"}, "the made-up account only")


class Readers(unittest.TestCase):
    def each(self):
        for version in samples.VERSIONS:
            for which in samples.SETS:
                for state in samples.STATES:
                    yield "%s %s" % (version, which), state, samples.screen(state, which, version=version)

    def test_who_has_the_keyboard(self):
        for which, state, view in self.each():
            box = state in BOX
            self.assertEqual(screen.pane_state(view), "box" if box else "dialog", (which, state))
            self.assertEqual(screen.panel_shown(view), not box, (which, state))
            self.assertEqual(screen.has_prompt(view), box, (which, state))
            span = screen.box_span(view)
            self.assertEqual(span is not None, box, (which, state))
            if box:  # one row, the last but two of the pane: under it its rule and the status line
                height = int(which.split("x")[1])
                self.assertEqual(span, (height - 3, height - 2), (which, state))

    def test_what_stands_in_the_box(self):
        """idle-suggestion: "run the tests" is Claude Code's suggestion, not typed text. input_text cannot tell (the
        last test here: only the colour does); this pins what it reads, not that the owner typed it."""
        for which, state, view in self.each():
            if state in BOX:
                self.assertEqual(screen.input_text(view), IN_BOX.get(state, ""), (which, state))

    def test_a_turn_running(self):
        """What the screen says, which is less than what is so: a turn also runs in busy-queued-typed,
        btw-answering-busy and btw-answer-busy (the spinner row is still up there), but with text in the box, or
        with the /btw answer box where the input box was, the status line no longer says "esc to interrupt".
        False here means "not to be seen on the screen", never "no turn runs": not a rule for stage 3b to keep,
        only what this function can know (#code-review 2026-10-11)."""
        for which, state, view in self.each():
            self.assertEqual(screen.busy_shown(view), state in ("busy", "busy-queued"), (which, state))

    def test_a_message_waiting_in_the_queue(self):
        for which, state, view in self.each():
            self.assertEqual(screen.send_now_shown(view), state in ("busy-queued", "busy-queued-typed"), (which, state))

    def test_the_side_answer(self):
        for which, state, view in self.each():
            answer = screen.btw_answer(view)
            if state in ANSWERED:
                self.assertTrue(answer and answer.startswith("The side answer"), (which, state, answer))
            else:
                self.assertFalse(answer, (which, state))

    def test_a_suggestion_is_told_from_typed_text_only_by_its_colour(self):
        for version in samples.VERSIONS:
            for size in ("120x50", "80x24"):
                at = (version, size)
                grey = samples.screen("idle-suggestion", "colour-" + size, esc=True, version=version)
                typed = samples.screen("idle-typed", "colour-" + size, esc=True, version=version)
                plain = samples.screen("idle-suggestion", "nocolor-" + size, esc=True, version=version)
                self.assertTrue(screen.input_dim(grey), at)
                self.assertFalse(screen.input_dim(typed), at)
                self.assertTrue(screen.colourless(plain), at)
                self.assertFalse(screen.colourless(grey), at)


class Extra(unittest.TestCase):
    """The 93 more screens of screens/2.1.296/extra (#code-review-2, while rehearsing the answer box and the
    startup dialogs): the box while it waits, second by second; under bypass mode with a turn running, a message
    queued, a background command; on panes down to 36 columns, where its hint is drawn on two or three rows; with an
    answer taller than it, scrolled; a question of 545 characters; and both startup dialogs on seven pane sizes."""

    def each(self):
        for group in samples.EXTRA:
            for state in samples.extra_states(group):
                yield group, state, samples.extra(group, state)

    def test_they_are_whole_and_say_what_they_are(self):
        seen = 0
        for group, state, view in self.each():
            meta = samples.extra_meta(group, state)
            width, height = meta["pane"]
            rows = view.split("\n")
            self.assertTrue(meta["claude_code"].startswith(samples.VERSION) and meta["how"], (group, state))
            self.assertEqual(len(rows), height + 1, (group, state))
            self.assertLessEqual(max(len(r) for r in rows), width, (group, state))
            flat = re.sub(r"\x1b\[[0-9;:]*m|\s", "", view + samples.extra(group, state, esc=True))
            self.assertFalse(re.search(r"claude-(?!1000001/)\d+/|/home/", flat), (group, state, "the made-up account only"))
            seen += 1
        self.assertEqual(seen, 109)

    def test_the_answer_box(self):
        for group, state, view in self.each():
            answer = screen.btw_answer(view)
            if group == "L":
                self.assertIsNone(answer, (group, state))
                self.assertIsNone(screen.btw_box(view), (group, state))
            elif "answering" in state or state.startswith(("btw-500-", "btw-429-")):
                self.assertEqual(answer, "", (group, state, "on its way, however long, whatever the spinner shows"))
            else:
                self.assertTrue(answer, (group, state))
                self.assertNotIn("Esc to close", answer, (group, state, "the hint, on however many rows, is not the answer's end"))
                self.assertNotIn("to browse", answer, (group, state))
                self.assertFalse(answer.startswith("/btw"), (group, state))
            self.assertEqual(screen.pane_state(view), "dialog", (group, state))

    def test_the_offer_to_clear_history(self):
        """Only once the conversation has had a side question before: the first one's hint does not offer it."""
        first = lambda group, state: group in ("B", "D", "L") or state.endswith("answer-first") or state == "answer-bypass" \
            or re.match(r"answering-bypass-\d+s$", state)  # noqa: E731
        for group, state, view in self.each():
            hint = screen.btw_hint(view)
            self.assertEqual("x to clear history" in hint, not first(group, state), (group, state, hint))
            if group != "L":
                self.assertTrue(hint.endswith("Esc to close") and "\n" not in hint, (group, state, hint))

    def test_a_tall_answer_is_read_as_far_as_the_box_shows_it(self):
        for kind, start in (("paragraphs", {"top": "Paragraph 01", "down7": "Paragraph 12", "bottom": "Paragraph 36"}),
                            ("list", {"top": "- item 01", "down7": "- item 22", "bottom": "- item 32"}),
                            ("code", {"top": "line 01", "down7": "line 22", "bottom": "line 32"})):
            for size in ("120x24", "70x24"):
                for where, words in start.items():
                    answer = screen.btw_answer(samples.extra("E", "e-%s-%s-%s" % (kind, size, where)))
                    self.assertTrue(answer.startswith(words), (kind, size, where, answer[:30]))

    def test_the_startup_dialogs_on_every_size(self):
        for group, state, view in self.each():
            self.assertEqual(screen.startup_key(view), "Down" if group == "L" else None, (group, state))


if __name__ == "__main__":
    unittest.main()
