"""Another machine's event stream while it reconnects (2026-10-07: the Mac back in the lab, typing got through at
once while the stream slept its 300 seconds, so messages were reported as maybe not sent and the late replies were
marked as typed at the terminal)."""
import asyncio
import os
import tempfile
import unittest
from unittest import mock

from bot import ask, config, remote, tmux_bridge
from tests import fakepane
from tests.test_turn_end import Ch, Host


class Proc(object):
    """An SSH line that brings one heartbeat, then stays open."""
    def __init__(self):
        self.stdout = self
        self.lines = [b'{"heartbeat": 1}\n']

    async def readline(self):
        if self.lines:
            return self.lines.pop(0)
        await asyncio.sleep(3600)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.saved = (ask.ASKS, ask.ANSWERS, config.OWNER_ID, config.INTERIM, remote.on_good)
        ask.ASKS, ask.ANSWERS = os.path.join(self.tmp.name, "asks"), os.path.join(self.tmp.name, "answers")
        config.OWNER_ID, config.INTERIM = 42, 0
        self.ch = Ch()
        self.bridge = tmux_bridge.TmuxBridge(Host(self.ch))
        remote.LAST_GOOD.clear()
        remote.TRIED.clear()

    def tearDown(self):
        ask.ASKS, ask.ANSWERS, config.OWNER_ID, config.INTERIM, remote.on_good = self.saved
        remote.LAST_GOOD.clear()
        remote.TRIED.clear()
        self.tmp.cleanup()


class Wake(Base):
    def test_a_command_getting_through_wakes_the_waiting_stream_at_once(self):
        up, probes = [False], []

        async def reach(host, timeout=20):
            probes.append(up[0])
            if not up[0]:
                remote.TRIED[host] = ["LAP"]
                return None
            remote._good(host, "LAP")
            return "LAP"

        async def once(host, alias, command, input, timeout):
            return 0, "ok", ""

        async def spawn(*args, **kw):
            return Proc()

        async def go():
            with mock.patch.object(remote, "reach", reach), mock.patch.object(remote, "_once", once), \
                    mock.patch.object(remote, "stream_command", return_value="x"), \
                    mock.patch.object(asyncio, "create_subprocess_exec", spawn), \
                    mock.patch.object(tmux_bridge, "STREAM_MIN_GAP", 0.1):
                task = asyncio.ensure_future(self.bridge.stream("lap"))
                await asyncio.sleep(0.2)
                self.assertEqual(probes, [False])
                self.assertIn("lap", self.bridge.stream_down)
                self.assertIn("lap", self.bridge.stream_wake, "waiting to reconnect")
                up[0] = True  # back in the lab: the next message typed gets through
                self.assertEqual(await remote.run("lap", "tmux send-keys ..."), (0, "ok"))
                await asyncio.sleep(0.3)  # far less than the 5 seconds it was waiting
                self.assertEqual(probes[:2], [False, True], "the stream tried again at once")
                self.assertNotIn("lap", self.bridge.stream_down, "events flow again")
                task.cancel()
        asyncio.run(go())

    def tries_while_commands_succeed(self, secs, gap=None):
        """A stream that ends as soon as it connects, while a command gets through every 0.05 s: how many tries."""
        tries = []

        async def reach(host, timeout=20):
            remote._good(host, "LAP")
            return "LAP"

        class Dead(object):
            def __init__(self):
                self.stdout = self

            async def readline(self):
                return b""

        async def spawn(*args, **kw):
            tries.append(1)
            return Dead()

        async def go():
            patches = [mock.patch.object(remote, "reach", reach), mock.patch.object(remote, "stream_command", return_value="x"),
                       mock.patch.object(asyncio, "create_subprocess_exec", spawn)]
            if gap is not None:
                patches.append(mock.patch.object(tmux_bridge, "STREAM_MIN_GAP", gap))
            for p in patches:
                p.start()
            try:
                task = asyncio.ensure_future(self.bridge.stream("lap"))
                t0 = asyncio.get_running_loop().time()
                while asyncio.get_running_loop().time() - t0 < secs:
                    remote._good("lap", "LAP")  # the channel in use: screens and keys get through
                    await asyncio.sleep(0.05)
                task.cancel()
            finally:
                for p in patches:
                    p.stop()
        asyncio.run(go())
        return len(tries)

    def test_a_stream_dying_at_once_is_not_restarted_at_every_command(self):
        self.assertEqual(self.tries_while_commands_succeed(1.5), 1, "tries at least 5 seconds apart")

    def test_a_woken_try_that_brought_nothing_ends_the_waking(self):
        self.assertEqual(self.tries_while_commands_succeed(2, gap=0.2), 2,
                         "the first, one woken; then the backoff's 5 seconds, not woken")

    def test_nothing_waiting_nothing_woken(self):
        self.bridge.reached("lap")  # no stream sleeping: nothing happens
        self.assertEqual(self.bridge.stream_wake, {})

    def test_the_longest_wait_is_a_minute(self):
        self.assertEqual(tmux_bridge.STREAM_MAX_DELAY, 60)


class Late(Base):
    def setUp(self):
        super().setUp()
        st = self.bridge.host.chan(self.ch)
        st.pop("tmux", None)
        st["pane"] = {"socket": "/tmp/s", "id": "%1", "host": "lap"}
        self.screens = []

        async def nothing(*a, **kw):
            return True

        async def screen(*a, **kw):
            self.screens.append(a)
            return "SCREEN"
        self.patches = [mock.patch.object(self.bridge, "ensure", nothing),
                        mock.patch.object(self.bridge, "close_panel", nothing),
                        mock.patch.object(self.bridge, "may_type", mock.AsyncMock(side_effect=lambda *a: tmux_bridge.Typing())),
                        mock.patch.object(self.bridge, "enter", nothing),
                        fakepane.replace("type_text", nothing), fakepane.replace("keys", nothing),
                        fakepane.replace("screen", screen), fakepane.guard(),
                        mock.patch.object(tmux_bridge, "RECEIPT_SECS", 0.05),
                        mock.patch.object(tmux_bridge, "SUBMIT_DELAY", 0)]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        super().tearDown()

    def message(self, text):
        class M(object):
            channel, content, reactions = self.ch, text, []

            async def add_reaction(self, emoji):
                self.reactions.append(emoji)
        return M()

    def test_stream_down_typed_in_reply_late_not_maybe_unsent(self):
        self.bridge.stream_down.add("lap")
        m = self.message("hello there")

        async def go():
            await self.bridge.submit(m, "hello there")
        asyncio.run(go())
        self.assertIn("已經打進去了", self.ch.sent[-1].content)
        self.assertIn("lap", self.ch.sent[-1].content)
        self.assertNotIn("可能沒有送進", self.ch.sent[-1].content)
        self.assertEqual(self.screens, [], "no screen posted")

    def test_stream_up_no_receipt_still_maybe_unsent_and_not_kept(self):
        async def go():
            await self.bridge.submit(self.message("hello there"), "hello there")
        asyncio.run(go())
        self.assertIn("可能沒有送進", self.ch.sent[-1].content)
        self.assertEqual(self.bridge.rt(self.ch).late, [], "kept only when the stream was down")

    def test_a_receipt_after_ten_minutes_is_the_terminals(self):
        self.bridge.stream_down.add("lap")
        m = self.message("繼續")

        async def go():
            await self.bridge.submit(m, "繼續")
            self.bridge.rt(self.ch).late[0].late -= tmux_bridge.LATE_SECS + 1
            await self.bridge.handle({"channel": "5", "hook_event_name": "UserPromptSubmit", "session_id": "a",
                                      "prompt": "繼續", "host": "lap"})
        asyncio.run(go())
        self.assertIn("終端機那邊輸入", self.ch.sent[-1].content)
        self.assertEqual(m.reactions, [], "no 👀 on the old one")
        self.assertEqual(self.bridge.rt(self.ch).late, [])

    def test_a_late_receipt_for_a_message_typed_is_not_the_terminals(self):
        self.bridge.stream_down.add("lap")
        m = self.message("hello there")

        async def go():
            await self.bridge.submit(m, "hello there")
            n = len(self.ch.sent)
            await self.bridge.handle({"channel": "5", "hook_event_name": "UserPromptSubmit", "session_id": "a",
                                      "prompt": "hello there", "host": "lap"})
            self.assertFalse(any("終端機那邊輸入" in s.content for s in self.ch.sent[n:]))
            self.assertEqual(m.reactions, ["👀"], "marked as received after all")
            self.assertEqual(self.bridge.rt(self.ch).late, [])
            await self.bridge.handle({"channel": "5", "hook_event_name": "UserPromptSubmit", "session_id": "a",
                                      "prompt": "something else", "host": "lap"})
            self.assertIn("終端機那邊輸入", self.ch.sent[-1].content, "what the bot did not type still is")
        asyncio.run(go())


if __name__ == "__main__":
    unittest.main()
