"""How a turn ends on Discord: failures with a retry button, quiet progress, a mention after a long turn,
and send-now for "!!" messages."""
import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))))
from altention import testing as _testing  # noqa: E402
_testing.guard_home()  # tests never touch the real home (altention/testing.py; 11-08: one left a real dcremote-owner)
import asyncio
import os
import tempfile
import time
import unittest
from unittest import mock

import discord

from bot import ask, config, tmux_bridge
from tests import fakepane


class Msg(object):
    def __init__(self, mid, content, view, silent, kw=None):
        self.id, self.content, self.view, self.silent, self.reactions = mid, content, view, silent, []
        self.kw = kw or {}  # what else it was sent with: allowed_mentions, reference (C-103's mentions)

    async def delete(self):
        pass

    async def edit(self, content=None, view=None, **kw):
        if content is not None:
            self.content = content
        self.view = view

    async def add_reaction(self, emoji):
        self.reactions.append(emoji)


class Ch(object):
    id, name = 5, "c"

    def __init__(self):
        self.sent = []

    async def send(self, content, view=None, silent=False, **kw):
        self.sent.append(Msg(len(self.sent) + 1, content, view, silent, kw))
        return self.sent[-1]

    def get_partial_message(self, mid):
        return next(m for m in self.sent if m.id == mid)

    async def typing(self):
        pass


class Host(object):
    def __init__(self, ch):
        self.ch, self.channels_state, self.audits = ch, {}, []

    def get_channel(self, cid):
        return self.ch

    def chan(self, ch):
        return self.channels_state.setdefault(str(ch.id), {"tmux": "s", "cwd": "/tmp"})

    def save(self):
        pass

    async def audit(self, text):
        self.audits.append(text)


class TurnEnd(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.saved = (ask.ASKS, ask.ANSWERS, config.OWNER_ID, config.INTERIM)
        ask.ASKS, ask.ANSWERS = os.path.join(self.tmp.name, "asks"), os.path.join(self.tmp.name, "answers")
        config.OWNER_ID, config.INTERIM = 42, 0
        self.ch = Ch()
        self.bridge = tmux_bridge.TmuxBridge(Host(self.ch))

    def tearDown(self):
        ask.ASKS, ask.ANSWERS, config.OWNER_ID, config.INTERIM = self.saved
        self.tmp.cleanup()

    def ev(self, kind, **kw):
        return dict({"channel": "5", "hook_event_name": kind, "session_id": "abc"}, **kw)

    def test_a_failure_ends_the_turn_and_offers_a_retry(self):
        async def go():
            await self.bridge.handle(self.ev("UserPromptSubmit", prompt="do it"))
            st = self.bridge.host.chan(self.ch)
            self.assertTrue(st["busy"])
            async def no_screen(st):
                return "", ""  # (Claude Code shows no wait of its own: the failure as ever, C-99a)
            self.bridge.quota.screens = no_screen
            with mock.patch.object(tmux_bridge.quota, "LOOKS", (0,)):
                await self.bridge.handle(self.ev("StopFailure", error="rate_limit",
                                                 last_assistant_message="API Error: 429 usage limit"))
                await asyncio.gather(*self.bridge.quota.working)
            self.assertFalse(st["busy"])
            msg = self.ch.sent[-1]
            self.assertIn("用量達到上限", msg.content)
            self.assertIn("429 usage limit", msg.content)
            self.assertFalse(msg.silent)
            self.assertEqual(msg.view.children[0].custom_id, "retry:5")
            self.assertIn("失敗", self.bridge.host.audits[-1])
            typed = []

            async def submit(message, text, out=None, now=False):
                typed.append((message.channel, text))
            self.bridge.submit = submit
            edited = []

            class Resp(object):
                async def edit_message(self, view=None):
                    edited.append(view)

            owner = type("U", (), {"id": 42})()

            class Click(object):
                type = discord.InteractionType.component
                data = {"custom_id": "retry:5"}
                channel, user, response = self.ch, owner, Resp()
            self.assertTrue(await self.bridge.on_interaction(Click()))
            self.assertEqual(typed, [(self.ch, tmux_bridge.RETRY)])
            self.assertEqual(edited, [None])
            Click.data = {"custom_id": "ask:1:0:0"}
            self.assertFalse(await self.bridge.on_interaction(Click()))
        asyncio.run(go())

    def test_a_long_turn_ends_without_a_mention(self):
        """C-103 (owner 2026-11-03): how long a turn took is no reason to ring; only what needs the owner is."""
        async def go():
            for secs in (5, 3600):
                await self.bridge.handle(self.ev("UserPromptSubmit", prompt="go"))
                self.bridge.rt(self.ch).turn.t0 = time.time() - secs
                await self.bridge.handle(self.ev("Stop", last_assistant_message="done"))
                self.assertNotIn("<@", self.ch.sent[-1].content, secs)
        asyncio.run(go())

    def test_progress_and_notes_along_the_way_are_silent(self):
        async def go():
            await self.bridge.handle(self.ev("UserPromptSubmit", prompt="go"))
            turn = self.bridge.rt(self.ch).turn
            turn.t0 -= 60
            turn.lines.append("Bash ls")
            await self.bridge.progress(self.ch, turn, self.bridge.lane(self.ch))
            await self.bridge.say(self.ch, "note", silent=True)
            await self.bridge.handle(self.ev("Stop", last_assistant_message="done"))
            self.assertEqual([m.silent for m in self.ch.sent], [True, True, True, False])  # typed at the terminal, progress, note, reply
        asyncio.run(go())

    def test_send_now_waits_for_the_queued_hint(self):
        rule = "─" * 60  # as Claude Code 2.1.295 draws it: the queued message, its hint, the spinner, over the box
        pressed, screens = [], ["working", "\n".join(["● working", "❯ hi", "  ctrl+x ctrl+s to send now", "✻ Working… (5s)", rule,
                                                         "❯ Press up to edit queued messages", rule, "  esc to interrupt"])]

        async def raw_screen(t):
            return screens.pop(0) if len(screens) > 1 else screens[0]

        async def keys(t, *names):
            pressed.append(names)
        with fakepane.use(raw_screen=raw_screen, keys=keys):
            msg = Msg(1, "hi", None, False)
            self.assertTrue(asyncio.run(self.bridge.send_now(msg, "s")))
            self.assertEqual((pressed, msg.reactions), ([("C-x", "C-s")], ["⚡"]))
            screens[:] = ["idle"]
            pressed.clear()
            tmux_sleep = asyncio.sleep

            async def quick(s):
                await tmux_sleep(0)
            tmux_bridge.asyncio.sleep = quick
            try:
                self.assertFalse(asyncio.run(self.bridge.send_now(msg, "s")))
            finally:
                tmux_bridge.asyncio.sleep = tmux_sleep
            self.assertEqual(pressed, [])


    def test_send_now_is_said_in_audit_whoever_asked(self):
        """C-176: every send-now pressed is one #audit line: the owner's !!, or a Claude's dc post --now (its ⚡ on its
        📨 card). Not pressed (nothing queued): nothing said."""
        from bot import posts
        card = Msg(5, "card", None, False)
        owner = Msg(1, "hi", None, False)
        owner.channel = self.ch
        cases = [(owner, tmux_bridge.NOW_OWNER % self.ch.name),
                 (posts.Origin(self.ch, auto=True, sender=7, sender_name="#st-1", card=card),
                  tmux_bridge.NOW_CLAUDE % (self.ch.name, "#st-1 的 Claude ")),
                 (posts.Origin(self.ch, auto=True, sender_name="HostB 上的 Claude（x）"),
                  tmux_bridge.NOW_CLAUDE % (self.ch.name, "HostB 上的 Claude（x）"))]
        for msg, said in cases:
            self.bridge.host.audits.clear()
            with mock.patch.object(tmux_bridge.cc_typing, "send_now", mock.AsyncMock(return_value=True)):
                self.assertTrue(asyncio.run(self.bridge.send_now(msg, "s")))
            self.assertEqual(self.bridge.host.audits, [said])
        self.assertEqual(card.reactions, ["⚡"], "a post's ⚡ on its card")
        self.assertIn("#st-1 的 Claude 要求立刻送出", tmux_bridge.NOW_CLAUDE % (self.ch.name, "#st-1 的 Claude "))
        self.bridge.host.audits.clear()
        with mock.patch.object(tmux_bridge.cc_typing, "send_now", mock.AsyncMock(return_value=False)):
            self.assertFalse(asyncio.run(self.bridge.send_now(Msg(1, "hi", None, False), "s")))
        self.assertEqual(self.bridge.host.audits, [])


if __name__ == "__main__":
    unittest.main()
