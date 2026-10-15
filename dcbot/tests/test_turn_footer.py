"""The small line under a reply (#issues 27, stage 3b step 2.2e): what the turn used and how long it took are the
turn's own. A turn cut short (Esc: Claude Code sends no event) no longer lends its tools and its time to the next
one; a turn that waited in a dialog for minutes keeps its own."""
import asyncio
import unittest
from unittest import mock

import bot  # noqa: F401  (first: it puts the engine's folder on the path; the connectors lie beside dcbot/)
from bot import tmux_bridge, tmuxio
from tests import tmuxguard  # noqa: F401,E402  (no test reaches a real tmux server)
from tests.test_turn_end import Ch, Host
from tests.test_turn_kinds import P, play

CTX = [50000, "claude-sonnet-5-5"]


def tool(name, pid=None, post=False, **more):
    ev = dict({"hook_event_name": "PostToolUse" if post else "PreToolUse", "tool_name": name, "session_id": "s1", "tool_use_id": "t-" + name}, **more)
    return dict(ev, prompt_id=pid) if pid else ev


def stop(text, pid=None, **more):
    ev = dict({"hook_event_name": "Stop", "last_assistant_message": text, "session_id": "s1"}, **more)
    return dict(ev, prompt_id=pid) if pid else ev


def foot(row):
    """The footer of the reply posted with an event ("" when it has none)."""
    last = row[0][-1].split("\n")[-1]
    return last[3:] if last.startswith("-# ") else ""


class CutShort(unittest.TestCase):
    def test_a_turn_cut_short_lends_nothing_to_the_next(self):
        """Esc while the first turn ran its tools: no Stop, no event at all. The owner's next message is another turn
        (another prompt_id): its reply says what it used itself. (It said "Bash×2, Read×1" before.)"""
        rows, st, rt, bridge, printed = play([P("first", prompt_id="a"), tool("Bash", "a"), tool("Bash", "a"),
                                              P("second", prompt_id="b"), tool("Read", "b"), stop("done.", "b")])
        self.assertEqual(foot(rows[-1]), "Read×1 · 0 秒")
        self.assertEqual((rows[3][3], rt.turn), ("second", None), "the open turn is the second from its first event on")

    def test_the_same_turn_goes_on_as_ever(self):
        """Events of one turn (one prompt_id), a message sent while it runs among them (it comes with the turn's id):
        one turn, one footer."""
        rows, st, rt, bridge, printed = play([P("first", prompt_id="a"), tool("Bash", "a"), P("and also this", prompt_id="a"), tool("Read", "a"), stop("done.", "a")])
        self.assertEqual(foot(rows[-1]), "Bash×1, Read×1 · 0 秒")
        self.assertEqual(rows[2][3], "first", "still the turn the first message began")

    def test_a_reply_with_no_turn_of_its_own_bears_no_one_elses(self):
        """A turn cut short, then a reply whose turn began with no event here (its id is another): no tools, no time,
        where it bore the cut turn's."""
        rows, st, rt, bridge, printed = play([P("first", prompt_id="a"), tool("Bash", "a"), stop("to something else.", "c")])
        self.assertEqual(foot(rows[-1]), "")

    def test_a_tool_call_of_another_turn_begins_that_turn(self):
        rows, st, rt, bridge, printed = play([P("first", prompt_id="a"), tool("Bash", "a"), tool("Read", "b"), stop("done.", "b")])
        self.assertEqual(foot(rows[-1]), "Read×1 · 0 秒")

    def test_a_failure_of_another_turn_is_not_said_of_the_one_cut_short(self):
        rows, st, rt, bridge, printed = play([P("first", prompt_id="a"), tool("Bash", "a"), {"hook_event_name": "StopFailure", "error": "rate_limit", "session_id": "s1", "prompt_id": "b"}])
        self.assertTrue(rows[-1][0][-1].startswith("⚠️ **這一輪中斷了：用量達到上限**"))
        self.assertTrue(any("… → 失敗" in a for a in bridge.host.audits), bridge.host.audits)

    def test_events_that_bring_no_id_are_one_turn_until_the_screen_was_quiet(self):
        """A Claude Code that sends no prompt_id: as before this step, events go to the turn that is open; what the
        owner says after the screen showed no work for a while is another turn."""
        events = [P("first"), tool("Bash"), P("second"), tool("Read"), stop("done.")]
        rows, st, rt, bridge, printed = play(events)
        self.assertEqual(foot(rows[-1]), "Bash×1, Read×1 · 0 秒", "as before")

        async def quiet_between():
            ch = Ch()
            bridge = tmux_bridge.TmuxBridge(Host(ch))
            bridge.last_reap = 1e12
            st, rt = bridge.host.chan(ch), bridge.rt(ch)
            with mock.patch.object(bridge, "watch", lambda *a, **k: None):
                for ev in events[:2]:
                    await bridge.handle(dict(ev, channel=str(ch.id)))
                await bridge.rest(st, rt)
                for ev in events[2:]:
                    await bridge.handle(dict(ev, channel=str(ch.id)))
            return ch.sent[-1].content
        self.assertTrue(asyncio.run(quiet_between()).endswith("-# Read×1 · 0 秒"))


class WaitingInADialog(unittest.TestCase):
    def drive(self, events, rest_after):
        async def go():
            ch = Ch()
            bridge = tmux_bridge.TmuxBridge(Host(ch))
            bridge.last_reap = 1e12
            st, rt = bridge.host.chan(ch), bridge.rt(ch)

            async def menu(c, s, ev):
                pass
            with mock.patch.object(bridge, "watch", lambda *a, **k: None), mock.patch.object(bridge.menus, "post", menu):
                for n, ev in enumerate(events):
                    await bridge.handle(dict(ev, channel=str(ch.id)))
                    if n == rest_after:
                        rt.turn.t0 -= 300  # five minutes pass in the dialog
                        await bridge.rest(st, rt)
                        self.assertEqual((st["busy"], rt.turn is not None, rt.turn.quiet, rt.turn.msg), (False, True, True, None))
            return ch.sent[-1].content, st, rt
        return asyncio.run(go())

    PERMISSION = {"hook_event_name": "Notification", "notification_type": "permission_prompt", "message": "Claude needs your permission", "session_id": "s1", "prompt_id": "a"}

    def test_a_turn_that_sat_in_a_dialog_keeps_its_tools_and_its_time(self):
        """A permission asked, and minutes before it is answered: the screen shows no work, the channel stops showing
        "typing". The turn then goes on and ends: its reply said only how full the context was (the turn had been
        forgotten); now what it used, and how long it took, the wait included."""
        last, st, rt = self.drive([P("go", prompt_id="a"), tool("Bash", "a"), self.PERMISSION, tool("Bash", "a", post=True), stop("done.", "a", context=CTX)], rest_after=2)
        self.assertTrue(last.endswith("-# Bash×1 · 300 秒 · context 5%"), last)
        self.assertEqual((st["busy"], rt.turn), (False, None))

    def test_it_is_shown_as_busy_again_when_it_goes_on(self):
        async def go():
            ch = Ch()
            bridge = tmux_bridge.TmuxBridge(Host(ch))
            bridge.last_reap = 1e12
            st, rt = bridge.host.chan(ch), bridge.rt(ch)
            watched = []
            with mock.patch.object(bridge, "watch", lambda *a, **k: watched.append(1)):
                await bridge.handle(dict(P("go", prompt_id="a"), channel=str(ch.id)))
                await bridge.rest(st, rt)
                await bridge.handle(dict(tool("Bash", "a"), channel=str(ch.id)))
            return st["busy"], rt.turn.quiet, len(watched)
        self.assertEqual(asyncio.run(go()), (True, False, 2))

    def test_the_watcher_rests_the_turn_and_does_not_forget_it(self):
        """watch_busy() itself, with a pane that is there and a screen that shows no work."""
        async def go():
            ch = Ch()
            bridge = tmux_bridge.TmuxBridge(Host(ch))
            st, rt = bridge.host.chan(ch), bridge.rt(ch)
            rt.turn = tmux_bridge.Turn("go")
            rt.turn.tools["Bash"] = 1
            st["busy"] = True
            naps = []

            async def nap(seconds):
                naps.append(seconds)

            async def alive(target):
                return True

            async def raw_screen(target):
                return "a dialog, or nothing at all"
            with mock.patch.object(tmux_bridge.asyncio, "sleep", nap), mock.patch.object(tmuxio, "alive", alive), mock.patch.object(tmuxio, "raw_screen", raw_screen), \
                    mock.patch.object(tmux_bridge.screen, "busy_shown", lambda text: False):
                await bridge.watch_busy(ch, st, rt)
            return st["busy"], rt.turn, len(naps)
        busy, turn, naps = asyncio.run(go())
        self.assertEqual((busy, turn.quiet, turn.tools, naps), (False, True, {"Bash": 1}, 3))

    def test_a_pane_that_is_gone_ends_the_turn_as_ever(self):
        async def go():
            ch = Ch()
            bridge = tmux_bridge.TmuxBridge(Host(ch))
            st, rt = bridge.host.chan(ch), bridge.rt(ch)
            rt.turn, st["busy"] = tmux_bridge.Turn("go"), True

            async def nap(seconds):
                pass

            async def alive(target):
                return False
            with mock.patch.object(tmux_bridge.asyncio, "sleep", nap), mock.patch.object(tmuxio, "alive", alive):
                await bridge.watch_busy(ch, st, rt)
            return st["busy"], rt.turn, ch.sent[-1].content
        busy, turn, said = asyncio.run(go())
        self.assertEqual((busy, turn), (False, None))
        self.assertIn("Claude 的行程結束了", said)


class HowFullTheContextIs(unittest.TestCase):
    def test_it_is_what_the_turns_end_brought_and_nothing_older(self):
        """A reply whose end read no usage says none (measured: the turn's last row is written a moment after its Stop
        is handled, so the first turn of a new conversation finds none; step 2.2f waits for the row). An older
        number is not shown in its place: it helped none of the cases measured."""
        rows, st, rt, bridge, printed = play([P("one", prompt_id="a"), stop("first.", "a", context=CTX), P("two", prompt_id="b"), stop("second.", "b"),
                                              P("three", prompt_id="c"), stop("third.", "c", context=[100000, "claude-sonnet-5-5"])])
        self.assertEqual([foot(rows[i]) for i in (1, 3, 5)], ["0 秒 · context 5%", "0 秒", "0 秒 · context 10%"])


if __name__ == "__main__":
    unittest.main()
