"""Claude Code's agents list on real screens (stage 3b step 2.4d, #issues 32): the left key on an empty input box
moves the pane's conversation to a background process and shows the list of background conversations; what is typed
into the list's box and entered starts a new one, which no channel follows. The screens are #code-review-2's captures
of 2.1.296 and 2.1.288 in two sizes (screens/<version>/agents/, README there), unchanged."""
import os
import unittest

from altention.connectors.claude_code import screen
from altention.tests.connectors.claude_code import samples

LISTS = {"list-one-row": "empty", "list-four-rows": "empty", "list-after-exit": "empty", "list-a-working": "empty",
         "left-while-busy-moved": "empty", "list-typed": "typed"}
FROM_LIST = ("opened-from-list", "opened-after-turn")  # a conversation started in the list, opened from it


class Agents(unittest.TestCase):
    def each(self):
        for version in samples.VERSIONS:
            for which in samples.AGENT_SETS:
                for state in samples.AGENT_STATES:
                    yield (version, which, state), state, samples.agents(state, which, version=version)

    def test_all_of_them_are_there_whole(self):
        for version in samples.VERSIONS:
            for which in samples.AGENT_SETS:
                folder = os.path.join(samples.HERE, version, "agents", which)
                self.assertEqual(sorted(n[:-len(".meta.json")] for n in os.listdir(folder) if n.endswith(".meta.json")), sorted(samples.AGENT_STATES))
                width, height = (int(n) for n in which.split("-")[1].split("x"))
                for state in samples.AGENT_STATES:
                    meta = samples.agents_meta(state, which, version=version)
                    self.assertTrue(meta["claude_code"].startswith(version))
                    self.assertEqual(meta["pane"], [width, height])
                    rows = samples.agents(state, which, version=version).split("\n")
                    self.assertEqual(len(rows), height + 1, (version, which, state))
                    self.assertIn("\x1b[", samples.agents(state, which, esc=True, version=version))

    def test_the_list_is_told_and_nothing_else_is_taken_for_it(self):
        for at, state, view in self.each():
            self.assertEqual(screen.agents_list(view), LISTS.get(state), at)
            self.assertEqual(screen.pane_state(view), "agents" if state in LISTS else "box", at)

    def test_the_list_by_its_status_line_or_by_its_header(self):
        """Both are there on every list captured. Either is enough: the status line cut off after its mode (a narrower
        pane, another permission mode's wording) leaves the header; the header gone leaves the status line."""
        for at, state, view in self.each():
            if state not in LISTS:
                continue
            rows = view.split("\n")
            foot = max(i for i, r in enumerate(rows) if r.strip())
            self.assertRegex(rows[foot], screen.AGENTS_FOOT, at)
            self.assertFalse(any(mark in rows[foot] for mark in screen.ORDINARY_FOOT), at)
            cut = "\n".join(rows[:foot] + ["  ⏵⏵ bypass permissions · …"] + rows[foot + 1:])
            self.assertEqual(screen.agents_list(cut), LISTS[state] if state != "list-typed" else "typed", at)
            head = next(i for i, r in enumerate(rows) if screen.AGENTS_HEAD.match(r))
            self.assertLess(head, screen.HEAD_ROWS, at)
            self.assertEqual(screen.agents_list("\n".join(rows[:head] + [""] + rows[head + 1:])), LISTS[state], at)
            neither = "\n".join(rows[:head] + [""] + rows[head + 1:foot] + ["  ⏵⏵ bypass permissions · …"] + rows[foot + 1:])
            self.assertIsNone(screen.agents_list(neither), "nothing of Claude Code's own says it is the list")

    BUSY = ("busy-before-left", "left-while-busy-waiting", "returned-a-busy", "returned-a-busy-again", "attach-view-busy")

    def test_busy_when_the_status_line_cuts_the_mark_short(self):
        """#issues 34: back from the background, the status line has "← for agents" in front of the mark, and at 80
        columns it ends "esc to inter…"; the running turn was taken for over. Every busy sample is busy, in every
        width and version, and no other is."""
        cut = 0
        for at, state, view in self.each():
            self.assertEqual(screen.busy_shown(view), state in self.BUSY, at)
            cut += screen.BUSY_MARK not in view and state in self.BUSY
        self.assertEqual(cut, 6, "the samples cut short: three states, two versions, at 80 columns")

    def test_how_narrow_a_pane_can_be(self):
        """The same states at 60, 70, 75 and 90 columns (2.1.296): a busy conversation that came back from the
        background is read as busy from 80 columns up (at 75 the line ends "esc to …", too short to be told from
        other words); one never moved, from 70 up (at 60 it ends "esc to …"). Nothing else is ever busy. The known
        limit is written in dcbot/GUIDE.md as #code-review-2 measured it on 2.1.296 (bypass mode): with a background shell the
        status line drops "(shift+tab to cycle)" and the mark stays whole at 75 and 80 columns."""
        moved = ("returned-a-busy", "returned-a-busy-again", "attach-view-busy")
        for which in samples.NARROW_SETS:
            width = int(which.split("-")[1].split("x")[0])
            for state in samples.AGENT_STATES:
                view = samples.agents(state, which)
                if state in moved:
                    want = width >= 80
                elif state in ("busy-before-left", "left-while-busy-waiting"):
                    want = width >= 70
                else:
                    want = False
                self.assertEqual(screen.busy_shown(view), want, (which, state, view.rstrip("\n").split("\n")[-1]))

    def test_the_cut_mark_in_the_conversation_or_the_box_is_text(self):
        """The words themselves, typed by the user or quoted in a reply, above the status line: not busy."""
        for at, state, view in self.each():
            if state in self.BUSY or screen.box_span(view) is None or state in LISTS:
                continue
            rows = view.split("\n")
            top = screen.box_span(view)[0]
            quoted = "\n".join(rows[:top - 1] + ["● 狀態列會被截成 · esc to inter…"] + rows[top - 1:])
            self.assertFalse(screen.busy_shown(quoted), at)
            typed = "\n".join(rows[:top] + ["❯ 為什麼最後是 · esc to inter…"] + rows[top + 1:])
            self.assertFalse(screen.busy_shown(typed), at)

    def test_cut_too_short_or_another_hint_is_not_busy(self):
        view = samples.agents("returned-a-busy", "colour-80x24")
        status = view.rstrip("\n").split("\n")[-1]
        self.assertTrue(status.endswith("· esc to inter…"), status)
        for other, busy in (("esc to inter…", True), ("esc to i…", True), ("esc to …", False), ("esc to…", False), ("esc t…", False),
                            ("esc to cancel…", False), ("esc to clear…", False), ("esc to interrupt", True), ("esc to inter", False)):
            changed = view.replace(status, status[:status.rindex("esc to inter…")] + other)
            self.assertEqual(screen.busy_shown(changed), busy, other)

    def test_a_conversation_that_speaks_of_the_list_is_not_the_list(self):
        """The words of the list in a conversation's own text (channels here discuss these screens), above the box
        and in it: an ordinary screen stays one."""
        said = ["  ⏵⏵ bypass permissions · enter to return · space to reply · ctrl+x to delete · ? for shortcuts",
                " ▝▝   ▝▝   1 awaiting input · 0 working · 0 completed", "enter to create · esc to clear · ctrl+f to find",
                "❯ describe a task for a new session", screen.MOVING]
        for at, state, view in self.each():
            if state in LISTS or state == "left-while-busy-waiting":
                continue
            rows = view.split("\n")
            box = screen.box_span(view)[0]
            above = "\n".join(rows[:box - 1 - len(said)] + ["● " + said[0]] + ["  " + line for line in said[1:]] + rows[box - 1:])
            self.assertEqual((screen.pane_state(above), screen.agents_list(above)), ("box", None), at)
            typed = "\n".join(rows[:box] + ["❯ " + said[0], "  " + said[2]] + rows[box + 1:])
            self.assertEqual((screen.pane_state(typed), screen.agents_list(typed)), ("box", None), at)
            if not screen.busy_shown(view):
                self.assertFalse(screen.moving_away(above), at)

    def test_the_headers_words_as_a_row_of_a_conversation_are_text(self):
        """#code-review 2026-10-14: an ordinary box with text in it has a status line that says only its mode
        ("⏸ manual mode on"), neither of a conversation's marks; with a whole row "3 awaiting input · 1 working · 2
        completed" among the first rows (a reply that quotes the list) the screen was taken for the list with text
        in its box, and the message refused with those words. On every such sample there is, both versions."""
        seen = 0
        for version in samples.VERSIONS:
            for which in samples.SETS:
                for state in samples.STATES:
                    view = samples.screen(state, which, version=version)
                    if screen.box_span(view) is None or any(mark in screen.under_box(view) for mark in screen.ORDINARY_FOOT):
                        continue
                    seen += 1
                    rows = view.split("\n")
                    for quoted in ("● 3 awaiting input · 1 working · 2 completed", "  3 awaiting input · 1 working · 2 completed",
                                   "3 awaiting input · 1 working · 2 completed"):
                        for at in range(screen.HEAD_ROWS):
                            if at >= screen.box_span(view)[0] - 1:
                                continue
                            said = "\n".join(rows[:at] + [quoted] + rows[at + 1:])
                            self.assertEqual((screen.agents_list(said), screen.pane_state(said)), (None, screen.pane_state(view)), (version, which, state, at, quoted))
        self.assertGreaterEqual(seen, 20)

    def test_the_left_key_pressed_while_a_tool_runs(self):
        for at, state, view in self.each():
            self.assertEqual(screen.moving_away(view), state == "left-while-busy-waiting", at)
        view = samples.agents("left-while-busy-waiting")
        self.assertEqual(screen.pane_state(view), "box", "an ordinary busy screen but for that row")
        self.assertFalse(screen.moving_away(view.replace(screen.MOVING, screen.MOVING + " (as the docs put it)")), "only the row as a whole")
        idle = samples.agents("returned-a-idle").split("\n")
        self.assertFalse(screen.moving_away("\n".join(idle[:8] + [screen.MOVING] + idle[9:])), "no turn running: a line somebody wrote")

    def test_a_conversation_started_from_the_list_says_so_in_its_header_only(self):
        """A conversation moved out of the pane, back on the screen, and one started from the list look alike but
        for the third row of the header; a `claude attach` view looks like either, by which it shows. Where the
        header has scrolled off (the small pane, after a turn) nothing tells them apart."""
        for at, state, view in self.each():
            if state in FROM_LIST:
                self.assertTrue(screen.list_started(view), at)
            elif not state.startswith("attach-view"):
                self.assertFalse(screen.list_started(view), at)
        for version in samples.VERSIONS:
            shown = [screen.list_started(samples.agents(s, "colour-120x50", version=version)) for s in ("attach-view", "attach-view-other")]
            self.assertEqual(sorted(shown), [False, True], "one view of the conversation moved, one of a conversation from the list")
        low = samples.agents("opened-from-list").split("\n")
        head = next(i for i, r in enumerate(low) if screen.LIST_STARTED.match(r))
        moved = low[:head] + [""] + low[head + 1:12] + [low[head]] + low[13:]
        self.assertFalse(screen.list_started("\n".join(moved)), "the same row further down is text")


if __name__ == "__main__":
    unittest.main()
