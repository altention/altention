"""Startup dialogs the bot does not answer for the owner (Claude Code 2.1.303's external CLAUDE.md imports): posted with
buttons, the opening waits on them (no START_SECS limit, up to ASK_SECS), a press checks the screen again and picks the
answer with the arrows and Enter. Real screens: altention/tests/connectors/claude_code/screens/2.1.303/extra/I/."""
import asyncio
import os
import unittest
from unittest import mock

import discord

from bot import config, startup_ask, tmux_bridge
from tests import tmuxguard  # noqa: F401,E402  (no test reaches a real tmux server)
from altention.tests.connectors.claude_code import samples

NO, YES = "No, disable external imports", "Yes, allow external imports"


def real(state):
    with open(os.path.join(samples.HERE, "2.1.303", "extra", "I", state + ".txt"), encoding="utf-8") as f:
        return f.read()


class Pane(object):
    remote = False

    def __init__(self, views):
        self.views, self.pressed, self.t = list(views), [], {"socket": "/x", "id": "%1"}

    async def standing(self):
        return "alive"

    async def raw_screen(self):
        return self.views.pop(0) if len(self.views) > 1 else self.views[0]

    async def keys(self, *names):
        self.pressed.extend(names)


class Msg(object):
    def __init__(self, ch, mid):
        self.ch, self.id = ch, mid


class Chan(object):
    def __init__(self):
        self.id, self.name, self.sent, self.edits = 7, "學習", [], []

    async def send(self, content, view=None):
        self.sent.append((content, [b.label for b in view.children] if view else None, [b.custom_id for b in view.children] if view else None))
        return Msg(self, 70 + len(self.sent))

    def get_partial_message(self, mid):
        ch = self

        class P(object):
            async def edit(self, content=None, view=None):
                ch.edits.append((mid, content, view))
        return P()


class Host(object):
    def __init__(self, ch):
        self.ch, self.audits = ch, []

    def get_channel(self, cid):
        return self.ch

    async def audit(self, text):
        self.audits.append(text)


class Bridge(object):
    def __init__(self, ch):
        self.host = Host(ch)
        self.startup = startup_ask.StartupAsk(self)


def run(coro):
    return asyncio.run(coro)


class Waiting(unittest.TestCase):
    def setUp(self):
        self.ch = Chan()
        self.bridge = Bridge(self.ch)
        self.rt = tmux_bridge.Runtime()
        self.sleeps = mock.patch.object(tmux_bridge.asyncio, "sleep", self.tick)
        self.sleeps.start()
        self.addCleanup(self.sleeps.stop)
        self.clock = [1000.0]
        self.time = mock.patch.object(tmux_bridge.time, "time", lambda: self.clock[0])
        self.time.start()
        self.addCleanup(self.time.stop)
        self.ready_at = None

    async def tick(self, secs):
        self.clock[0] += secs
        if self.ready_at is not None and self.clock[0] >= self.ready_at:
            self.rt.ready.set()

    def wait(self, pane):
        ask = lambda choice: self.bridge.startup.post(self.ch, pane, choice)  # noqa: E731
        return run(tmux_bridge.TmuxBridge.wait_ready(self.bridge, pane, self.rt, ask=ask))

    def test_the_owner_deciding_for_longer_than_a_start_may_take_is_waited_on_asked_once(self):
        pane = Pane([real("imports-colour-120x50")])
        self.ready_at = 1000 + tmux_bridge.START_SECS * 3  # the owner answers in tmux after three START_SECS
        self.assertTrue(self.wait(pane))
        self.assertEqual(len(self.ch.sent), 1, "asked once")
        content, labels, ids = self.ch.sent[0]
        self.assertEqual(labels, [NO, YES])
        self.assertTrue(all(i.startswith("sta:") for i in ids))
        self.assertIn("Allow external CLAUDE.md file imports?", content)
        self.assertIn("秘密防護擋不到", content)
        self.assertEqual(pane.pressed, [], "the bot answers nothing itself")
        self.assertEqual([e[2] for e in self.ch.edits], [None], "its buttons go once the conversation is up")
        self.assertEqual(self.bridge.startup.open, {})

    def test_not_answered_for_ask_secs_the_opening_is_given_up_and_said(self):
        pane = Pane([real("imports-nocolor-80x24")])
        with mock.patch.object(startup_ask, "ASK_SECS", tmux_bridge.START_SECS * 2):
            self.assertFalse(self.wait(pane))
        self.assertIn("等太久了", self.ch.edits[-1][1])

    def test_the_trust_dialog_is_still_answered_by_the_bot_and_nothing_asked(self):
        pane = Pane([samples.screen("trust-dialog"), samples.screen("idle-fresh")])
        self.ready_at = 1003
        self.assertTrue(self.wait(pane))
        self.assertEqual(self.ch.sent, [])
        self.assertTrue(pane.pressed)

    def test_a_dialog_shown_without_anyone_to_ask_is_waited_on_as_before(self):
        pane = Pane([real("imports-colour-80x24")])
        self.assertFalse(run(tmux_bridge.TmuxBridge.wait_ready(self.bridge, pane, self.rt)))
        self.assertEqual(self.clock[0] - 1000 >= tmux_bridge.START_SECS, True)
        self.assertEqual(self.ch.sent, [])


class Opening(Waiting):
    """#code-review of 5b4d448: a channel with a saved conversation whose opening waited ASK_SECS on the owner did not
    stop there: open() went on to a new conversation in its place, which asked the same again, while the first
    message said the next message would open it. Now the opening ends; the saved conversation stays the channel's."""

    def test_given_up_on_the_owner_no_new_conversation_is_begun(self):
        started, failed = [], []
        pane = Pane([real("imports-colour-120x50")])

        async def start(resume, how):
            started.append(resume)
            return pane

        async def kept(p, fresh):
            self.fail("not kept")

        async def failed_(p):
            failed.append(p)

        bridge = self.bridge
        async def chosen(st):
            return ("opus", "bypassPermissions")
        bridge.chosen_for = chosen
        bridge.wait_ready = lambda where, rt, ask=None: tmux_bridge.TmuxBridge.wait_ready(bridge, where, rt, ask=ask)
        async def say(ch, text):
            pass
        bridge.say = say
        st = {"session_id": "saved-1"}
        with mock.patch.object(startup_ask, "ASK_SECS", tmux_bridge.START_SECS * 2):
            got = run(tmux_bridge.TmuxBridge.open(bridge, self.ch, st, self.rt, start, kept, failed_))
        self.assertFalse(got)
        self.assertEqual(started, ["saved-1"], "the saved conversation only, no new one after it")
        self.assertEqual(len(failed), 1)
        self.assertEqual(len(self.ch.sent), 1, "asked once")
        self.assertIn("再發一則訊息會重新開", self.ch.edits[-1][1])

    def test_a_start_that_fails_without_asking_still_falls_back_to_a_new_conversation(self):
        started = []
        pane = Pane([samples.screen("busy")])  # never ready, no dialog to ask about

        async def start(resume, how):
            started.append(resume)
            return pane

        async def nothing(*a):
            pass

        bridge = self.bridge
        async def chosen(st):
            return ("opus", "bypassPermissions")
        bridge.chosen_for = chosen
        bridge.wait_ready = lambda where, rt, ask=None: tmux_bridge.TmuxBridge.wait_ready(bridge, where, rt, ask=ask)
        run(tmux_bridge.TmuxBridge.open(bridge, self.ch, {"session_id": "saved-1"}, self.rt, start, nothing, nothing))
        self.assertEqual(started, ["saved-1", None], "as before")


class Press(unittest.TestCase):
    def setUp(self):
        self.ch = Chan()
        self.bridge = Bridge(self.ch)

    def click(self, token, i, who=None):
        got = []
        clicker = type("U", (), {"id": config.OWNER_ID if who is None else who})()
        ch = self.ch

        class Response(object):
            async def defer(self):
                got.append("defer")

            async def edit_message(self, view=None):
                got.append(("edit", view))

            async def send_message(self, text, ephemeral=False):
                got.append(("say", text))

        class Click(object):
            type = discord.InteractionType.component
            data = {"custom_id": "sta:%s:%d" % (token, i)}
            user = clicker
            channel = ch
            response = Response()
        return run(self.bridge.startup.on_interaction(Click())), got

    def post(self, pane, state):
        from altention.connectors.claude_code import screen
        return run(self.bridge.startup.post(self.ch, pane, screen.startup_choice(real(state))))

    def test_yes_is_down_then_enter_on_the_dialog_still_there(self):
        for start, keys in (("imports-colour-120x50", ["Down", "Enter"]), ("imports-yes-picked-colour-80x24", ["Enter"])):
            with self.subTest(start=start):
                pane = Pane([real(start)])
                token = self.post(pane, start)
                with mock.patch.object(startup_ask.tmuxio, "pane", lambda t, p=pane: p):
                    handled, got = self.click(token, 1)
                self.assertTrue(handled)
                self.assertEqual(pane.pressed, keys)
                self.assertIn("✅ 選了「%s」" % YES, self.ch.edits[-1][1])
                self.assertIsNone(self.ch.edits[-1][2])
                self.assertIn("選了「%s」" % YES, self.bridge.host.audits[-1])

    def test_no_from_yes_picked_is_up_then_enter(self):
        pane = Pane([real("imports-yes-picked-nocolor-120x50")])
        token = self.post(pane, "imports-yes-picked-nocolor-120x50")
        with mock.patch.object(startup_ask.tmuxio, "pane", lambda t: pane):
            self.click(token, 0)
        self.assertEqual(pane.pressed, ["Up", "Enter"])

    def test_the_dialog_gone_nothing_pressed_and_only_the_owner(self):
        pane = Pane([real("imports-colour-120x50")])
        token = self.post(pane, "imports-colour-120x50")
        handled, got = self.click(token, 1, who=config.OWNER_ID + 1)
        self.assertEqual(got, [("say", "只有擁有者能用。")])
        pane.views = [real("imports-answered-colour-120x50")]
        with mock.patch.object(startup_ask.tmuxio, "pane", lambda t: pane):
            self.click(token, 1)
        self.assertEqual(pane.pressed, [])
        self.assertIn("已經不在畫面上", self.ch.edits[-1][1])
        handled, got = self.click(token, 1)  # pressed again: an old message
        self.assertEqual(got, [("edit", None)])

    def test_other_buttons_are_not_ours(self):
        class Other(object):
            type = discord.InteractionType.component
            data = {"custom_id": "key:1:2"}
        self.assertFalse(run(self.bridge.startup.on_interaction(Other())))
