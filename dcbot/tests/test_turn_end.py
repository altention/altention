"""How a turn ends on Discord: failures with a retry button, quiet progress, a mention after a long turn,
and send-now for "!!" messages."""
import asyncio
import os
import tempfile
import time
import unittest

import discord

from bot import ask, config, tmux_bridge
from tests import fakepane


class Msg(object):
    def __init__(self, mid, content, view, silent, allowed=None):
        self.id, self.content, self.view, self.silent, self.reactions = mid, content, view, silent, []
        self.allowed = allowed

    async def delete(self):
        pass

    async def add_reaction(self, emoji):
        self.reactions.append(emoji)


class Ch(object):
    id, name = 5, "c"

    def __init__(self):
        self.sent = []

    async def send(self, content, view=None, silent=False, allowed_mentions=None, files=None):
        self.sent.append(Msg(len(self.sent) + 1, content, view, silent, allowed_mentions))
        self.sent[-1].files = files
        return self.sent[-1]

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
            await self.bridge.handle(self.ev("StopFailure", error="rate_limit",
                                             last_assistant_message="API Error: 429 usage limit"))
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

    def test_a_long_turn_ends_with_a_mention_and_a_short_one_does_not(self):
        async def go():
            for secs, mention in ((5, False), (tmux_bridge.LONG_TURN + 5, True)):
                await self.bridge.handle(self.ev("UserPromptSubmit", prompt="go"))
                self.bridge.rt(self.ch).turn.t0 = time.time() - secs
                await self.bridge.handle(self.ev("Stop", last_assistant_message="done"))
                self.assertEqual("<@42>" in self.ch.sent[-1].content, mention)
                allowed = self.ch.sent[-1].allowed  # P0-1: the client pings nobody; the footer's send lets the owner through
                self.assertEqual(allowed.to_dict() if allowed else None, {"parse": [], "users": [42]} if mention else None)
        asyncio.run(go())

    def test_a_long_turns_mention_still_rings_when_its_picture_cannot_be_sent(self):
        """P0-1 (#code-review on 1c0d6f85): the last message falls back to its text alone; it keeps the footer and may
        still ping the owner."""
        async def go():
            await self.bridge.post_reply(self.ch, {}, [("done", ["/nonexistent/picture.png"])],
                                         {"foot": "-# 11 分鐘 · <@42>", "final": "done"})
        asyncio.run(go())
        last = self.ch.sent[-1]
        self.assertIn("<@42>", last.content)
        self.assertEqual(last.allowed.to_dict() if last.allowed else None, {"parse": [], "users": [42]})

    def test_a_long_turns_mention_rings_on_the_message_with_its_picture(self):
        """P0-1 (#code-review-2 on 1c0d6f85): the footer on the last message, sent with its picture."""
        with tempfile.TemporaryDirectory() as d:
            pic = os.path.join(d, "chart.png")
            with open(pic, "wb") as f:
                f.write(b"\x89PNG\r\n")

            async def go():
                await self.bridge.post_reply(self.ch, {}, [("done", [pic])], {"foot": "-# 11 分鐘 · <@42>", "final": "done"})
            asyncio.run(go())
        last = self.ch.sent[-1]
        self.assertEqual(len(last.files or []), 1, "sent with its picture, not the fallback")
        self.assertIn("<@42>", last.content)
        self.assertEqual(last.allowed.to_dict() if last.allowed else None, {"parse": [], "users": [42]})

    def test_progress_and_notes_along_the_way_are_silent(self):
        async def go():
            await self.bridge.handle(self.ev("UserPromptSubmit", prompt="go"))
            turn = self.bridge.rt(self.ch).turn
            turn.t0 -= 60
            await self.bridge.progress(self.ch, turn, "Bash ls")
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


if __name__ == "__main__":
    unittest.main()
