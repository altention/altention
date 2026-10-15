"""A pane's conversation moved to a background process (the left key on an empty input box, Claude Code's agents
list; #issues 32, stage 3b step 2.4d). The hook lets its events through and marks them (tests/test_routes.py); here:
what the bot does with them. Played from the measured runs (the connector's hook_payloads/sessions/left*.json, both
versions), cut to what the hook lets through and marked as it marks them."""
import asyncio
import contextlib
import io
import json
import os
import tempfile
import unittest
from unittest import mock

import bot  # noqa: F401  (first: it puts the engine's folder on the path; the connectors lie beside dcbot/)
from altention.tests.connectors.claude_code.test_normalize import VERSIONS
from altention.tests.connectors.claude_code import samples
from bot import config, tmux_bridge, tmuxio
from tests import tmuxguard  # noqa: F401,E402  (no test reaches a real tmux server)
from tests.test_session_kinds import ATTACHED, ENDED_PANE, OWN, WINDOW, load, play
from tests import fakepane, test_safety, test_stream_wake  # (its class by the module: a name here would have its tests run again)

MOVED_ENDED = "ℹ️ 這個頻道的對話先前被搬到背景"
WHERE = [None, "/s", "%1"]  # ATTACHED's pane, as the channel remembers where a conversation was moved out of


def through_the_hook(version, name):
    """The sample's events that reach the channel: the pane's own, and those of the conversation it was moved to,
    marked. (The other background processes of the run name no pane and no route: the hook drops them.)"""
    events = load(version, name)
    old, new = events[0]["session_id"], next(ev["session_id"] for ev in events if ev.get("source") == "fork")
    return [dict(ev, dc_parked=True) if ev["session_id"] == new else ev for ev in events if ev["session_id"] in (old, new)], old, new


def as_the_bots_own(version, shape):
    """The run started as the bot starts its own windows ("leftown"), as it reaches the bot: the pane's own events
    say their pane; so do those of the conversation that was moved, placed by the hook and marked; the other background
    processes', which all name the channel by its variable, come with no pane and marked as not placed (the fourth
    value). `shape` the channel's window."""
    events = load(version, "leftown")
    old, new = events[0]["session_id"], next(ev["session_id"] for ev in events if ev.get("source") == "fork")
    pane = {"socket": "/tmp/tmux-1/" + config.TMUX_SOCKET, "pane": "%1"} if shape.get("tmux") else {"socket": "/s", "pane": "%1"}
    out = [dict(ev, dc_pane=pane, **({"dc_parked": True} if ev["session_id"] == new else {})) for ev in events if ev["session_id"] in (old, new)]
    return out, old, new, [dict(ev, dc_unplaced=True) for ev in events if ev["session_id"] not in (old, new)]


class StateFolder(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state, config.STATE = config.STATE, os.path.join(self.tmp.name, "state")

    def tearDown(self):
        config.STATE = self.state
        self.tmp.cleanup()

    def noted(self):
        with open(os.path.join(config.STATE, tmux_bridge.PARKED), encoding="utf-8") as f:
            return json.load(f)["channels"]


class Moved(StateFolder):
    def test_a_turn_moved_half_way_is_the_same_turn_under_the_new_id(self):
        for version in VERSIONS:
            events, old, new = through_the_hook(version, "leftbusy")
            rows, shown, ready, st, printed = play(events[:8], ATTACHED)
            self.assertEqual([k for k, _, _ in rows], ["ready", "input", "turn_end", "input", "progress", "session_changed", "turn_end", "agent_stop"])
            self.assertEqual([sid for _, _, sid in rows], [old] * 5 + [new] * 3, "the channel's conversation is the one it was moved to, from its start")
            self.assertEqual(rows[5][1], [], "the move itself says nothing in the channel")
            self.assertEqual(rows[6][1], ["ANS-busy.\n-# Bash×1 · 0 秒"], "its reply, with the tool the turn ran before it was moved")
            self.assertFalse(st.get("busy"), "and the turn is over: no end ever comes under the old id")
            self.assertEqual((shown, printed), ([], ""), "no history is posted for it, nothing is printed")
            self.assertIn(old, json.dumps(st), "the id it had can still be taken up")

    def test_what_is_said_after_it_goes_on_in_the_channel(self):
        for version in VERSIONS:
            for name, replies in (("leftbusy", ["ANS-one.", "ANS-busy.", "ANS-after-busy."]),
                                  ("leftesc", ["ANS-one.", "ANS-after-esc.", "ANS-after-enter."])):
                events, old, new = through_the_hook(version, name)
                rows = play(events, ATTACHED)[0]
                said = [t.split("\n")[0] for k, texts, _ in rows if k == "turn_end" for t in texts]
                self.assertEqual(said, replies, (version, name))

    def test_the_channel_remembers_it_and_writes_it_down(self):
        for version in VERSIONS:
            events, old, new = through_the_hook(version, "leftnew")   # typed into the list afterwards: nothing more comes
            rows, _, _, st, _ = play(events[:4], ATTACHED)
            self.assertEqual(rows[-1][0], "session_changed")
            rec = tmux_bridge.parked(st)
            self.assertEqual((rec["session"], rec["pane"], rec.get("unanswered")), (new, WHERE, None))
            (noted,) = self.noted()
            self.assertEqual((noted["session"], noted["unanswered"]), (new, None))
            self.assertEqual(sorted(noted), ["channel", "session", "since", "unanswered"])

    def test_its_end_is_not_said_as_the_end_of_the_pane(self):
        for version in VERSIONS:
            events, old, new = through_the_hook(version, "leftesc")
            rows, _, _, st, _ = play(events, ATTACHED)
            ends = [texts for k, texts, _ in rows if k == "session_ended"]
            self.assertEqual(len(ends), 2, "the conversation is stopped where it was moved to; later the pane is closed")
            self.assertTrue(ends[0][0].startswith(MOVED_ENDED), ends[0])
            self.assertNotIn("重新開 Claude 就會自動接回", ends[0][0], "the pane's Claude Code is still there")
            self.assertTrue(ends[1][0].startswith(ENDED_PANE), ends[1])
            self.assertIsNone(tmux_bridge.parked(st), "the pane's own Claude Code ended: nothing is moved any more")
            self.assertEqual(self.noted(), [])

    def test_a_new_claude_code_in_the_pane_ends_it(self):
        events, old, new = through_the_hook(VERSIONS[0], "leftnew")
        again = dict(events[0], session_id="5af70cea-b596-4333-b5aa-6e2bd380d008")
        rows, _, _, st, _ = play(events[:4] + [again], ATTACHED)
        self.assertEqual((rows[-1][0], rows[-1][2]), ("ready", again["session_id"]))
        self.assertIsNone(tmux_bridge.parked(st))
        self.assertEqual(self.noted(), [])

    def test_it_holds_for_the_pane_it_was_said_of(self):
        events, old, new = through_the_hook(VERSIONS[0], "leftnew")
        st = play(events[:4], ATTACHED)[3]
        self.assertTrue(tmux_bridge.parked(st))
        st["pane"] = {"socket": "/s", "id": "%2"}   # the channel was attached to another pane since
        self.assertIsNone(tmux_bridge.parked(st))
        for odd in ({}, {"parked": 5, "pane": {}}, {"parked": {"session": "x"}}, {"parked": {"pane": WHERE}, "home": {}}):
            self.assertIsNone(tmux_bridge.parked(odd), odd)

    def test_another_machines_pane_alike(self):
        events, old, new = through_the_hook(VERSIONS[0], "leftbusy")
        shape = {"pane": {"socket": "/s", "id": "%1", "host": "lap"}}
        rows, _, _, st, _ = play(events[:8], shape, host="lap")
        self.assertEqual(rows[6][1][0].split("\n")[0], "ANS-busy.")
        self.assertEqual(tmux_bridge.parked(st)["pane"], ["lap", "/s", "%1"])

    def test_events_that_are_not_marked_change_nothing(self):
        """A pane whose conversation was never moved: no record, and no file is written for it."""
        events = load(VERSIONS[0], "clear")
        st = play(events, ATTACHED)[3]
        self.assertNotIn("parked", st)
        self.assertFalse(os.path.exists(os.path.join(config.STATE, tmux_bridge.PARKED)))


class OwnWindow(StateFolder):
    """A window the bot opened: every background process started from it has the channel's variable and the bot's
    hooks. Until this step the spare process's start overwrote the channel's session id the moment the left key
    was pressed, and the turns of a conversation typed into the list were posted in the channel."""

    def test_the_channel_keeps_to_the_conversation_that_was_moved(self):
        for version in VERSIONS:
            for shape in (OWN, WINDOW):
                events, old, new, others = as_the_bots_own(version, shape)
                rows, shown, ready, st, printed = play(events, shape)
                self.assertEqual([sid for _, _, sid in rows], [old] * 3 + [new] * 7, (version, shape))
                everything = [t for _, texts, _ in rows for t in texts]
                self.assertEqual([t.split("\n")[0] for k, texts, _ in rows if k == "turn_end" for t in texts], ["ANS-one.", "ANS-A-after-move."])
                self.assertFalse([t for t in everything if "-B-" in t], "nothing of the conversation typed into the list")
                self.assertEqual((shown, printed), ([], ""))
                ends = [texts for k, texts, _ in rows if k == "session_ended"]
                if shape is WINDOW:
                    self.assertTrue(ends[0][0].startswith(MOVED_ENDED), "stopped where it was moved to: the window is still there")
                    self.assertIn("`!reset`", ends[0][0])
                    self.assertTrue(ends[1][0].startswith("ℹ️ 這個頻道的 Claude 結束了"), "then the window itself")
                else:
                    self.assertTrue(ends[0][0].startswith(MOVED_ENDED))
                    self.assertEqual(ends[1], [], "the bot's own session: reopened without a word, as before")
                self.assertIsNone(tmux_bridge.parked(st))

    def test_the_other_background_processes_leave_the_channel_alone(self):
        """In the order they really came (the spare process starts the moment the left key is pressed, before or
        after the moved conversation; the one typed into the list runs two turns): the channel's session is never
        theirs, nothing of theirs is posted, a line is written for each of them once."""
        for version in VERSIONS:
            for shape in (OWN, WINDOW):
                events, old, new, others = as_the_bots_own(version, shape)
                mine, theirs, both = list(events), list(others), []
                for ev in load(version, "leftown"):   # put back in the run's own order
                    both.append(mine.pop(0) if ev["session_id"] in (old, new) else theirs.pop(0))
                rows, shown, ready, st, printed = play(both, shape)
                self.assertEqual({sid for _, _, sid in rows}, {old, new}, (version, shape))
                self.assertEqual(rows[-1][2], new)
                self.assertFalse([t for _, texts, _ in rows for t in texts if "-B-" in t], (version, shape))
                self.assertEqual([t.split("\n")[0] for k, texts, _ in rows if k == "turn_end" for t in texts], ["ANS-one.", "ANS-A-after-move."])
                self.assertEqual(len([texts for k, texts, _ in rows if k == "session_ended" and texts]), 2 if shape is WINDOW else 1,
                                 "the end of the moved conversation and of the window: not one for each background process")
                with open(os.path.join(config.STATE, tmux_bridge.UNPLACED_LOG), encoding="utf-8") as f:
                    lines = f.read().splitlines()
                self.assertEqual(len(lines), len({ev["session_id"] for ev in others}), "one line a conversation")
                self.assertIn("session %s channel" % others[0]["session_id"], lines[0])
                os.remove(os.path.join(config.STATE, tmux_bridge.UNPLACED_LOG))

    def test_either_order_of_the_spare_process_and_the_moved_conversation(self):
        for version in VERSIONS:
            events, old, new, others = as_the_bots_own(version, OWN)
            spare = next(ev for ev in others if ev["hook_event_name"] == "SessionStart")
            for seq in (events[:3] + [spare, events[3]], events[:3] + [events[3], spare]):
                st = play(seq, OWN)[3]
                self.assertEqual(st["session_id"], new, version)

    def test_the_moved_conversation_ends_its_turn_after_its_window_is_gone(self):
        """Measured on 2.1.296: the window's Claude Code ended (the tmux session closed, Ctrl-C twice in the list)
        while the moved conversation ran a tool; it runs on under the daemon and ends the turn. No process says any
        more where it came from, so its events come not placed; the channel has its id from while the window was
        there, and its reply is posted, as it was before this step."""
        for version in VERSIONS:
            for shape in (OWN, WINDOW):
                events, old, new, others = as_the_bots_own(version, shape)
                moved = [ev for ev in events if ev["session_id"] == new]
                gone = dict(events[-1])   # the window's own SessionEnd
                late = [dict({k: v for k, v in ev.items() if k not in ("dc_pane", "dc_parked")}, dc_unplaced=True) for ev in moved[2:]]
                rows, _, _, st, _ = play(events[:3] + moved[:2] + [gone] + late + others, shape)
                self.assertEqual([t.split("\n")[0] for k, texts, _ in rows if k == "turn_end" for t in texts], ["ANS-one.", "ANS-A-after-move."], (version, shape))
                self.assertEqual(st["session_id"], new)
                self.assertFalse([t for _, texts, _ in rows for t in texts if "-B-" in t])

    def test_an_unplaced_event_is_not_waited_for_and_makes_no_runtime(self):
        events, old, new, others = as_the_bots_own(VERSIONS[0], OWN)
        ch = test_safety.Ch()
        bridge = tmux_bridge.TmuxBridge(test_safety.Host(ch))
        st = bridge.host.chan(ch)
        st["session_id"] = new
        tool = dict(next(ev for ev in others if ev["hook_event_name"] == "PreToolUse"), channel=str(ch.id), ts=1e12)
        self.assertFalse(bridge.waits(tool))
        self.assertEqual(bridge.rts, {})
        self.assertTrue(tmux_bridge.unplaced(st, tool))
        self.assertFalse(tmux_bridge.unplaced(st, dict(tool, session_id=new)))
        self.assertFalse(tmux_bridge.unplaced(st, {k: v for k, v in tool.items() if k != "dc_unplaced"}), "an event not marked is never judged by this")
        self.assertTrue(tmux_bridge.unplaced({}, tool), "a channel with no session yet: a background process is not its first")

    def test_a_question_or_a_plan_of_theirs_goes_back_to_the_terminal(self):
        ch = test_safety.Ch()
        bridge = tmux_bridge.TmuxBridge(test_safety.Host(ch))
        bridge.host.chan(ch)["session_id"] = "78cb859a-4150-4c2e-b027-033964dcd9b7"
        handed = []

        async def deliver_to(host, ask_id, result):
            handed.append((host, ask_id, result))
        with mock.patch.object(tmux_bridge.ask, "deliver_to", deliver_to):
            for name, ask_id in (("Ask", "a1"), ("Plan", "p1")):
                asyncio.run(bridge.handle({"hook_event_name": name, "ask_id": ask_id, "questions": [], "plan": "x", "channel": str(ch.id),
                                           "session_id": "e27ee4fd-b08e-46bf-8e17-f06f3ddd8ba5", "dc_unplaced": True}))
        self.assertEqual(handed, [(None, "a1", {"pass": True}), (None, "p1", {})])
        self.assertEqual(ch.sent, [], "nothing is shown in the channel")

    def test_the_record_holds_for_a_session_of_the_bots_own_too(self):
        events, old, new, _ = as_the_bots_own(VERSIONS[0], OWN)
        st = play(events[:4], OWN)[3]
        self.assertEqual(tmux_bridge.parked(st)["pane"], ["tmux", OWN["tmux"]])
        self.assertEqual(self.noted()[0]["session"], new)
        st["tmux"] = "dc-x-2"
        self.assertIsNone(tmux_bridge.parked(st))


class Typed(test_stream_wake.Late):
    """A message typed into a pane whose conversation was moved, and no word of it comes: the pane may be showing
    another background conversation, or the list. (Late's channel: another machine's pane, typing stood in for; its
    own tests are not run again here.)"""
    locals().update({name: None for name in dir(test_stream_wake.Late) if name.startswith("test_")})

    def run_one(self, parked):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        was, config.STATE = config.STATE, os.path.join(tmp.name, "state")
        self.addCleanup(setattr, config, "STATE", was)
        st = self.bridge.host.chan(self.ch)
        if parked:
            st["parked"] = {"session": "78cb859a-4150-4c2e-b027-033964dcd9b7", "since": 5, "pane": ["lap", "/tmp/s", "%1"]}

        async def go():
            await self.bridge.submit(self.message("hello there"), "hello there")
        asyncio.run(go())
        return st, self.ch.sent[-1].content

    def test_the_channel_is_told_the_conversation_was_moved(self):
        st, said = self.run_one(True)
        self.assertIn("這個窗格的對話已被搬到背景；窗格現在可能在看別的背景對話，或停在清單。請在終端機確認它顯示的是原本的對話", said)
        self.assertIn("SCREEN", said, "with the screen as it is")
        self.assertNotIn("!reset", said, "nothing of what is said for a pane that shows the channel's conversation")
        self.assertTrue(st["parked"]["unanswered"])
        with open(os.path.join(config.STATE, tmux_bridge.PARKED), encoding="utf-8") as f:
            (noted,) = json.load(f)["channels"]
        self.assertEqual(noted["unanswered"], st["parked"]["unanswered"])

    def test_a_word_from_the_conversation_takes_that_back(self):
        st, _ = self.run_one(True)
        ev = {"hook_event_name": "UserPromptSubmit", "prompt": "later", "session_id": st["parked"]["session"],
              "channel": str(self.ch.id), "host": "lap", "dc_parked": True}
        with mock.patch.object(self.bridge, "watch", lambda *a, **k: None):
            asyncio.run(self.bridge.handle(ev))
        self.assertNotIn("unanswered", st["parked"])
        with open(os.path.join(config.STATE, tmux_bridge.PARKED), encoding="utf-8") as f:
            self.assertIsNone(json.load(f)["channels"][0]["unanswered"])

    def test_a_pane_whose_conversation_was_not_moved_is_told_as_before(self):
        st, said = self.run_one(False)
        self.assertIn("⚠️ 訊息可能沒有送進 Claude。目前的畫面", said)
        self.assertFalse(os.path.exists(os.path.join(config.STATE, tmux_bridge.PARKED)))


class List(unittest.TestCase):
    """Nothing is typed into Claude Code's agents list: what is entered there starts a new background conversation no
    channel follows (#issues 32: a message from Discord went into one). On the real screens (the connector's
    screens/<version>/agents/, both versions, both sizes)."""

    def setUp(self):
        tmuxio._esc_at.clear()
        self.ch = test_safety.Ch()
        self.bridge = tmux_bridge.TmuxBridge(test_safety.Host(self.ch))
        self.st = self.bridge.host.chan(self.ch)

    def each(self):
        for version in samples.VERSIONS:
            for which in samples.AGENT_SETS:
                yield (version, which), lambda state, v=version, w=which: samples.agents(state, w, version=v)

    def send(self, screens, text="hello", **more):
        async def nothing(*a, **k):
            pass

        class Message(object):
            channel, id, content = self.ch, 1, text
        self.ch.sent.clear()
        fake = fakepane.Screens(screens, echo=True)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            test_safety.run(self.bridge.submit(Message(), text), fake, mock.patch.object(self.bridge, "ensure", nothing),
                            mock.patch.object(tmux_bridge, "SUBMIT_DELAY", 0), mock.patch.object(tmux_bridge, "RECEIPT_SECS", 0.01),
                            fakepane.replace("ESC_GAP", 0.01), *[mock.patch.object(tmux_bridge.cc_typing if hasattr(tmux_bridge.cc_typing, k) else tmux_bridge, k, v)
                              for k, v in more.items()])  # (the typing's own waits are the connector's since 2.7b-1)
        return fake.log, "\n".join(m.content for m in self.ch.sent)

    def attached(self):
        self.st.pop("tmux", None)
        self.st["pane"] = {"socket": "/tmp/x", "id": "%1"}

    def test_an_attached_pane_gets_no_key_and_the_owner_is_told(self):
        self.attached()
        for at, screen_of in self.each():
            for state in ("list-one-row", "list-four-rows", "list-after-exit", "list-a-working", "left-while-busy-moved"):
                for busy in (False, True):
                    self.st["busy"] = busy
                    log, said = self.send([screen_of(state)])
                    self.assertEqual(log, [], (at, state, busy))
                    self.assertIn("這個窗格在背景對話的清單畫面，請在終端機按 Esc 回到對話", said)
                    self.assertIn("沒有送出", said)
                    self.assertEqual(self.bridge.rt(self.ch).outcome.code, "claude_code.agents_list")

    def test_a_list_whose_box_holds_text_gets_no_key_in_any_window(self):
        for shape in ("own", "home", "attached"):
            self.setUp()
            if shape != "own":
                self.attached()
            if shape == "home":
                self.st["home"] = {"socket": "/tmp/x", "session": "work", "window": "w"}
            for at, screen_of in self.each():
                log, said = self.send([screen_of("list-typed")])
                self.assertEqual(log, [], (shape, at))
                self.assertIn("清單的輸入框裡已經有字", said)
                self.assertIn("沒有送出", said)
                self.assertEqual(self.bridge.rt(self.ch).outcome.code, "claude_code.list_has_text")

    def test_a_window_the_bot_opened_gets_one_escape_and_the_message_goes_once_the_list_is_gone(self):
        for shape in ("own", "home"):
            self.setUp()
            if shape == "home":
                self.attached()
                self.st["home"] = {"socket": "/tmp/x", "session": "work", "window": "w"}
            for at, screen_of in self.each():
                tmuxio._esc_at.clear()
                lst = screen_of("list-one-row")
                log, said = self.send([lst, lst, lst, screen_of("returned-a-idle")])
                self.assertEqual(log, [("key", "Escape"), ("typed", "hello"), ("key", "Enter")], (shape, at))
                self.assertNotIn("沒有送出", said)

    def test_a_list_that_stays_after_the_escape_is_not_typed_into(self):
        for at, screen_of in self.each():
            tmuxio._esc_at.clear()
            log, said = self.send([screen_of("list-four-rows")])
            self.assertEqual(log, [("key", "Escape")], at)
            self.assertIn("按了一次 Esc 之後畫面也沒有回到對話的輸入框", said)
            self.assertIn("沒有送出", said)
            self.assertEqual(self.bridge.rt(self.ch).outcome.code, "claude_code.list_not_left")

    def test_a_screen_between_two_frames_after_the_escape_is_waited_out(self):
        """#code-review 2026-10-14: the first screen that was not the list was gone on with; one drawn half way (no
        box on it) read as a dialog, and the message was refused with "no key was pressed" after the Escape."""
        for at, screen_of in self.each():
            tmuxio._esc_at.clear()
            lst, half = screen_of("list-one-row"), "\n".join(screen_of("returned-a-idle").split("\n")[:5])
            self.assertEqual(tmux_bridge.screen.pane_state(half), "dialog")
            log, said = self.send([lst, lst, lst, half, half, screen_of("returned-a-idle")])
            self.assertEqual(log, [("key", "Escape"), ("typed", "hello"), ("key", "Enter")], at)
            self.assertNotIn("沒有送出", said)
            tmuxio._esc_at.clear()
            log, said = self.send([lst, lst, lst, half])   # and never a box: said as what it is, one Escape and no more
            self.assertEqual(log, [("key", "Escape")], at)
            self.assertIn("按了一次 Esc 之後", said)
            self.assertNotIn("也沒有按任何鍵", said)

    def test_the_list_coming_up_after_the_typing_gets_no_enter(self):
        """The left key pressed in the terminal between the bot's look and its Enter: the text stands in the list's
        box, where Enter would start a conversation with it."""
        self.attached()
        for at, screen_of in self.each():
            typed = screen_of("list-typed").replace("half a task typed, not sent", "hello")

            class Moved(fakepane.Screens):  # an ordinary screen until something is typed, then the list with it in its box
                async def raw_screen(inner, name):
                    inner.last = typed if any(entry[0] == "typed" for entry in inner.log) else screen_of("returned-a-idle")
                    return inner.last

            async def nothing(*a, **k):
                pass

            class Message(object):
                channel, id, content = self.ch, 1, "hello"
            fake = Moved([""])
            self.ch.sent.clear()
            test_safety.run(self.bridge.submit(Message(), "hello"), fake, mock.patch.object(self.bridge, "ensure", nothing),
                            mock.patch.object(tmux_bridge, "SUBMIT_DELAY", 0), mock.patch.object(tmux_bridge, "RECEIPT_SECS", 0.01))
            self.assertEqual(fake.log, [("typed", "hello")], at)
            self.assertIn("沒有按 Enter", "\n".join(m.content for m in self.ch.sent))

    def test_about_to_move_is_waited_for_and_then_it_is_the_list(self):
        self.attached()
        for at, screen_of in self.each():
            waiting = screen_of("left-while-busy-waiting")
            self.st["busy"] = True
            log, said = self.send([waiting, waiting, waiting, screen_of("left-while-busy-moved")])
            self.assertEqual(log, [], at)
            self.assertIn("這個窗格在背景對話的清單畫面", said)
            log, said = self.send([waiting], MOVING_LOOKS=3)
            self.assertEqual(log, [], at)
            self.assertIn("正要搬到背景", said)
            self.assertIn("沒有送出", said)
            self.assertEqual(self.bridge.rt(self.ch).outcome.code, "claude_code.moving_away")

    def test_another_background_conversation_on_the_screen_of_a_pane_whose_own_was_moved(self):
        self.attached()
        for at, screen_of in self.each():
            for state in ("opened-from-list", "opened-after-turn"):
                self.st["parked"] = {"session": "78cb859a-4150-4c2e-b027-033964dcd9b7", "since": 5, "pane": [None, "/tmp/x", "%1"]}
                log, said = self.send([screen_of(state)])
                self.assertEqual(log, [], (at, state))
                self.assertIn("另一個背景對話", said)
                self.assertIn("沒有送出", said)
                self.assertEqual(self.bridge.rt(self.ch).outcome.code, "claude_code.other_conversation")
                self.st.pop("parked")
                log, said = self.send([screen_of(state)])
                self.assertEqual(log[:2], [("typed", "hello"), ("key", "Enter")], "a pane whose conversation was not moved: its header is its own matter")
            self.st["parked"] = {"session": "78cb859a-4150-4c2e-b027-033964dcd9b7", "since": 5, "pane": [None, "/tmp/x", "%1"]}
            for state in ("returned-a-idle", "left-while-busy-returned"):
                log, said = self.send([screen_of(state)])
                self.assertEqual(log[:2], [("typed", "hello"), ("key", "Enter")], (at, state, "back in the conversation that was moved"))

    def test_an_ordinary_screen_goes_as_before(self):
        self.attached()
        for at, screen_of in self.each():
            log, said = self.send([screen_of("a-idle-before-left")])
            self.assertEqual(log[:2], [("typed", "hello"), ("key", "Enter")], at)


if __name__ == "__main__":
    unittest.main()
