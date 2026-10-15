"""What the bot does around the three places of the screen read only where Claude Code draws them (the /btw answer,
"x to clear history", the dialogs Claude Code opens with): asking, a request tried again, starting. What the
connector reads off those screens is tested beside it (altention/tests/connectors/claude_code/test_screen_words.py,
there since stage 3b step 1.3), and the screens and helpers come from there."""
import asyncio
import unittest
from unittest import mock

import bot  # noqa: F401  (first: it puts the engine's folder on the path; the connectors lie beside dcbot/)
from altention.connectors.claude_code import screen
from altention.tests.connectors.claude_code import samples
from altention.tests.connectors.claude_code.test_screen_words import ANSWER, LOOKALIKE, WHOLE, each, over
from bot import tmux_bridge
from tests import fakepane
from tests.test_turn_end import Ch, Host


class Ask(unittest.TestCase):
    """btw_ask() on those screens: what is posted, which keys are pressed."""

    def ask(self, screens, pane=False):
        ch = Ch()
        bridge = tmux_bridge.TmuxBridge(Host(ch))
        st = bridge.host.chan(ch)
        if pane:
            st.pop("tmux")
            st["pane"] = {"socket": "/tmp/x", "id": "%1"}
        shown, pressed, typed, sent, reads = list(screens), [], [], [], []
        self.reads_at_escape = None

        async def raw_screen(name):
            reads.append(1)
            if not sent:  # the input box, with the question once it is typed
                rule = "─" * 120
                return "\n".join(["● earlier", "", rule, "❯ " + (typed[0] if typed else ""), rule, "  ? for shortcuts", ""])
            return shown[0] if len(shown) == 1 else shown.pop(0)

        async def keys(name, *names):
            pressed.extend(names)
            if "Enter" in names:
                sent.append(1)
            if "Escape" in names:
                self.reads_at_escape = self.reads_at_escape or len(reads)
                shown[:] = [samples.screen("btw-closed")]

        async def type_text(name, text):
            typed.append(text)

        async def nothing(*a, **k):
            return None

        async def color_screen(name):
            return (await raw_screen(name)), 120

        async def no(name):
            return False

        async def nowhere(name):
            return None
        with fakepane.guard(), fakepane.replace("raw_screen", raw_screen), fakepane.replace("keys", keys), \
                fakepane.replace("type_text", type_text), fakepane.replace("color_screen", color_screen), \
                fakepane.replace("in_mode", no), fakepane.replace("vim_mode", no), \
                fakepane.replace("cursor", nowhere), fakepane.replace("ESC_GAP", 0.01), \
                mock.patch.object(bridge, "ensure", nothing), mock.patch.object(bridge, "btw_copy", nothing), \
                mock.patch.object(tmux_bridge, "SUBMIT_DELAY", 0), mock.patch.object(tmux_bridge, "BTW_SECS", 3), \
                mock.patch.object(tmux_bridge.asyncio, "sleep", mock.AsyncMock()):
            asyncio.run(bridge.btw_ask(ch, None, "is anything wrong?"))
        return "\n".join(m.content for m in ch.sent), pressed

    def test_the_plain_case(self):
        for pane in (False, True):
            said, pressed = self.ask([samples.screen("btw-answering"), samples.screen("btw-answer")], pane)
            self.assertTrue(said.startswith(WHOLE), said)
            self.assertEqual([k for k in pressed if k in ("x", "Escape")], ["Escape"], "no history to clear: no x")

    def test_an_answer_saying_answering_is_posted_and_closed_once(self):
        answered = samples.screen("btw-answer").replace(ANSWER, "While it waits the box shows · Answering…")
        for pane in (False, True):
            said, pressed = self.ask([samples.screen("btw-answering"), answered], pane)
            self.assertTrue(said.startswith("While it waits the box shows · Answering…"), said)
            self.assertNotIn("沒有看到答案框", said)

    def test_a_quoted_box_is_neither_posted_nor_is_the_real_one_closed(self):
        waiting = over(samples.screen("btw-answering"), LOOKALIKE)
        for pane in (False, True):
            said, pressed = self.ask([waiting], pane)  # the real answer does not come within the time
            self.assertIn("還沒有答案", said)
            self.assertNotIn("（沒有內容）", said)
            self.assertNotIn("An answer quoted in a reply.", said.split("目前的畫面")[0])
            self.assertNotIn("Escape", pressed, "the box, still answering, is left open: said so, not closed")
            said, pressed = self.ask([waiting, waiting, over(samples.screen("btw-answer"), LOOKALIKE)], pane)
            self.assertTrue(said.startswith(WHOLE), said)

    def test_x_only_on_the_boxs_own_offer(self):
        for pane in (False, True):
            first = over(samples.screen("btw-answer").replace(ANSWER, "Press x to clear history."),
                         ["● The hint reads: x to clear history · Esc to close", ""])
            said, pressed = self.ask([first], pane)
            self.assertNotIn("x", pressed, "the words in the answer and in the conversation are not the box's offer")
            said, pressed = self.ask([samples.screen("btw-answer-second")], pane)
            self.assertEqual([k for k in pressed if k in ("x", "Escape")], ["x", "Escape"])
            rows = samples.screen("btw-answer-second", "colour-80x24").split("\n")  # a pane 70 columns wide
            last = max(i for i, r in enumerate(rows) if r.strip())
            two = rows[1:last] + ["    ⇧←/→ to browse · c to copy · f to fork · x to clear history ·", "    Esc to close"] + rows[last + 1:]
            said, pressed = self.ask(["\n".join(two)], pane)
            self.assertEqual([k for k in pressed if k in ("x", "Escape")], ["x", "Escape"], "the offer on the hint's first row")


class Retrying(unittest.TestCase):
    """#code-review-2 2026-10-12, on 2.1.296: when the /btw request fails and Claude Code tries again, the row under
    the question reads "· API error · retrying in 2s · attempt 3/10" (or "Rate limited · …"), not "Answering…".
    It was taken for the answer: posted a second after sending, and the box, still retrying, closed. An answered
    box is now told by its hint offering "c to copy", whatever the rows in it say."""

    def each(self, code):
        for state in samples.extra_states("B"):
            if state.startswith("btw-%s-" % code):
                yield state, samples.extra("B", state)

    def test_retrying_is_still_on_its_way(self):
        seen = 0
        for code in ("500", "429"):
            for state, view in self.each(code):
                self.assertIn("retrying in", view)
                self.assertEqual(screen.btw_answer(view), "", state)
                self.assertNotIn("c to copy", screen.btw_hint(view), state)
                seen += 1
        self.assertEqual(seen, 8)

    def test_claude_codes_last_word_on_a_request_that_failed_is_the_answer(self):
        for code, words in (("529", "(API error: Repeated 529 Overloaded errors."), ("400", "(API error: 400")):
            for state, view in self.each(code):
                self.assertTrue(screen.btw_answer(view).startswith(words), state)

    def test_the_words_of_the_hint_elsewhere_decide_nothing(self):
        waiting = samples.screen("btw-answering")
        for row in ("      The hint will offer c to copy · Esc to close", "      · API error · retrying in 2s · attempt 3/10",
                    "      c to copy"):
            said = waiting.replace("      · Answering…", row)
            self.assertNotEqual(said, waiting)
            self.assertEqual(screen.btw_answer(said), "", "in the box's rows: %r" % row)
        asked = waiting.replace("/btw is anything wrong?", "/btw what does c to copy · Esc to close mean?")
        self.assertEqual(screen.btw_answer(asked), "", "in the question itself")
        self.assertEqual(screen.btw_answer(over(waiting, ["● The box's hint: ↑/↓ to scroll · c to copy · Esc to close", ""])), "", "in the conversation")
        answered = samples.screen("btw-answer").replace(ANSWER, "Still retrying in 2s · attempt 3/10, it said.")
        self.assertTrue(screen.btw_answer(answered).startswith("Still retrying in 2s"), "an answer that talks of retrying is an answer")

    def test_the_offer_on_a_narrow_pane(self):
        """The hint wraps anywhere between two words: also inside "c to copy"."""
        rows = samples.screen("btw-answer", "colour-80x24").split("\n")
        last = max(i for i, r in enumerate(rows) if r.strip())
        for first, second in (("↑/↓ to scroll · c to", "copy · f to fork · Esc to close"), ("↑/↓ to scroll · c", "to copy · f to fork · Esc to close"),
                              ("↑/↓ to scroll ·", "c to copy · f to fork · Esc to close")):
            view = "\n".join(rows[1:last] + ["    " + first, "    " + second] + rows[last + 1:])
            self.assertTrue(screen.btw_answer(view).startswith(ANSWER), first)
        for group in ("C", "W"):
            for state in samples.extra_states(group):
                answered = "answering" not in state
                self.assertEqual(bool(screen.btw_answer(samples.extra(group, state))), answered, (group, state))

    def ask(self, screens, pane):
        return Ask.ask(self, screens, pane)

    def test_asking_while_it_retries_posts_nothing_of_it_and_closes_nothing(self):
        retrying = [samples.extra("B", "btw-500-2s"), samples.extra("B", "btw-500-8s"), samples.extra("B", "btw-500-20s")]
        for pane in (False, True):
            said, pressed = self.ask(retrying, pane)
            self.assertIn("還沒有答案", said)
            self.assertIn("沒有關它", said)
            self.assertNotIn("retrying in", said.split("目前的畫面")[0], "the row is not posted as an answer")
            self.assertEqual([k for k in pressed if k in ("c", "x", "Escape")], [], "no key into a box that has not answered")
            said, pressed = self.ask(retrying[:2] + [samples.screen("btw-answer")], pane)  # the retry gets through
            self.assertTrue(said.startswith(WHOLE), said)
            self.assertEqual([k for k in pressed if k == "Escape"], ["Escape"])
            said, pressed = self.ask([samples.extra("B", "btw-529-8s")], pane)
            self.assertTrue(said.startswith("(API error: Repeated 529 Overloaded errors."), said)


class StartupDialog(unittest.TestCase):
    def test_the_real_dialogs(self):
        for state in ("trust-dialog", "bypass-dialog"):
            for which, view in each(state):
                self.assertEqual(screen.startup_key(view), "Down", (state, which, "No is the one picked"))
                rows = view.split("\n")
                no = next(i for i, r in enumerate(rows) if "❯ No, exit" in r)
                rows[no], rows[no + 1] = rows[no].replace("❯", " "), rows[no + 1].replace("  Yes", "❯ Yes", 1)
                self.assertEqual(screen.startup_key("\n".join(rows)), "Enter", (state, which, "Yes picked"))
        for state in samples.STATES:
            if state not in ("trust-dialog", "bypass-dialog"):
                for which, view in each(state):
                    self.assertIsNone(screen.startup_key(view), (state, which))

    def test_numbered_answers(self):
        rule = "─" * 60
        for picked, key in (("❯ 1. Yes, I trust this folder\n   2. No, exit", "Enter"), ("  1. Yes, I trust this folder\n ❯ 2. No, exit", "Down")):
            self.assertEqual(screen.startup_key("\n%s\n Do you trust the files in this folder?\n\n %s\n\n Enter to confirm · Esc to cancel\n"
                                                     % (rule, picked)), key)

    QUOTED = ["❯ what does the dialog offer? No, exit… Yes or no", "", "● It offers:", "", "  ❯ No, exit", "    Yes, I trust this folder", ""]

    def test_a_resumed_conversation_that_quotes_the_dialog(self):
        """Was: a Down every half second until SessionStart was handled (the box at the bottom is the ordinary one)."""
        for state in ("idle-after-turns", "idle-bypass", "busy", "idle-suggestion"):
            for which, view in each(state):
                self.assertIsNone(screen.startup_key(over(view, self.QUOTED)), (state, which))

    def test_the_words_with_no_input_box_drawn_yet(self):
        """A frame with the conversation there and the box not yet: the quoted answers are not the screen's last rows."""
        view = "\n".join([""] + self.QUOTED + ["● And more of the reply after them.", "", "✻ Baked for 2s", ""])
        self.assertEqual(screen.pane_state(view), "dialog", "nothing but a dialog can be told here")
        self.assertIsNone(screen.startup_key(view))
        for which, view in each("permission"):
            self.assertIsNone(screen.startup_key(over(view, self.QUOTED)), which + ": another dialog's answers")
        for which, view in each("model-panel"):
            self.assertIsNone(screen.startup_key(over(view, self.QUOTED)), which)

    def test_wait_ready_presses_nothing_on_a_resumed_conversation(self):
        ch = Ch()
        bridge = tmux_bridge.TmuxBridge(Host(ch))
        pressed, view = [], over(samples.screen("idle-bypass"), self.QUOTED)

        async def raw_screen(name):
            return view

        async def keys(name, *names):
            pressed.extend(names)
        real = asyncio.sleep

        async def nap(seconds):
            await real(0.005)

        async def go():
            rt = bridge.rt(ch)
            rt.ready.clear()
            asyncio.get_running_loop().call_later(0.2, rt.ready.set)  # SessionStart handled late
            return await bridge.wait_ready("s", rt)
        with fakepane.replace("raw_screen", raw_screen), fakepane.replace("keys", keys), \
                fakepane.replace("alive", mock.AsyncMock(return_value=True)), mock.patch.object(asyncio, "sleep", nap):
            self.assertTrue(asyncio.run(go()))
            self.assertEqual(pressed, [])
            view = samples.screen("bypass-dialog")
            self.assertTrue(asyncio.run(go()))
            self.assertTrue(pressed and set(pressed) == {"Down"}, "the real dialog is still answered")


if __name__ == "__main__":
    unittest.main()
