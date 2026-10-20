"""pump(): every channel's output goes through it, so nothing may end it."""
import asyncio
import contextlib
import io
import json
import os
import tempfile
import time
import unittest
from unittest import mock

from bot import events, tmux_bridge
from tests import tmuxguard  # noqa: F401,E402  (no test reaches a real tmux server)


class Host(object):
    channels_state = {}

    def chan(self, ch):
        return {}

    def save(self):
        pass

    async def audit(self, text):
        raise OSError("Discord unreachable")  # what a dropped connection looks like (not HTTPException)


class Pump(unittest.TestCase):
    def test_handle_and_audit_both_failing_do_not_end_the_round(self):
        spool = tempfile.mkdtemp()
        with open(os.path.join(spool, "01.json"), "w") as f:
            json.dump({"channel": "1", "hook_event_name": "Stop"}, f)
        bridge = tmux_bridge.TmuxBridge(Host())
        bridge.last_reap = 1e18

        async def broken(ev):
            raise ConnectionResetError("connection reset")

        async def nothing():
            return None
        bridge.handle = broken
        bridge.agents.tick = nothing
        with mock.patch.object(tmux_bridge, "SPOOL", spool):
            asyncio.run(bridge.pump_once())  # must return, not raise
        self.assertEqual(os.listdir(spool), [], "the bad event is out of the queue")
        self.assertEqual(os.listdir(spool + "-bad"), ["01.net.json"], "an outage: not held against the version")
        self.assertFalse(bridge.handling)

    def test_which_failures_are_outages(self):
        class HTTPException(Exception):
            def __init__(self, status):
                self.status = status
        for exc, net in ((ConnectionResetError(), True), (TimeoutError(), True), (asyncio.TimeoutError(), True),
                         (HTTPException(503), True), (HTTPException(429), True), (HTTPException(403), False),
                         (FileNotFoundError(), False), (OSError(), False), (KeyError(), False)):
            self.assertEqual(events.transient(exc), net, repr(exc))


class NoWaitingOnOthers(unittest.TestCase):
    """C-73: a post whose Claude never takes it (its machine does not answer) does not hold up the round; a slow
    round is said in supervisor.out with the part that took the time."""
    def bridge(self):
        bridge = tmux_bridge.TmuxBridge(Host())
        bridge.last_reap = bridge.streams_checked = 1e18

        async def nothing():
            return None
        bridge.agents.tick = nothing
        return bridge

    def test_a_post_for_a_machine_that_never_answers_does_not_hold_the_round(self):
        from bot import postbox, posts
        from tests.test_posts import Bot, Chan
        spool, events_spool = tempfile.mkdtemp(), tempfile.mkdtemp()
        bot = Bot()
        far = Chan(2, "far")
        bot.chans, bot.guild.text_channels = {2: far}, [far]
        bot.channels_state = {"2": {"tmux": "dc-far"}}

        class Never(object):
            async def submit(self, message, text):
                await asyncio.Event().wait()  # ssh to a machine that is off, never coming back
        bot.tmux = Never()
        host = Host()
        host.posts = posts.Posts(bot)
        bridge = self.bridge()
        bridge.host = host
        postbox.write({"channel": "far", "text": "hi", "from_name": "x"}, spool)

        async def go():
            for _ in range(3):
                t0 = time.time()
                await bridge.pump_once()
                self.assertLess(time.time() - t0, 0.5)
                await asyncio.sleep(0.05)
            self.assertEqual(far.sent, ["📨 來自 x\nhi"], "shown; only its handing over waits")
        with mock.patch.object(postbox, "SPOOL", spool), mock.patch.object(tmux_bridge, "SPOOL", events_spool):
            asyncio.run(go())

    def test_a_slow_round_is_said_with_its_slow_part(self):
        bridge = self.bridge()

        async def slow():
            await asyncio.sleep(0.4)
        bridge.agents.tick = slow
        out = io.StringIO()
        with mock.patch.object(tmux_bridge, "SPOOL", tempfile.mkdtemp()), mock.patch.object(tmux_bridge, "SLOW_ROUND", 0.3), \
                contextlib.redirect_stdout(out):
            asyncio.run(bridge.pump_once())
        self.assertRegex(out.getvalue(), r"^\[pump\] 一圈 0\.\d 秒：子代理 0\.4\n$")
        out = io.StringIO()
        bridge.agents.tick = lambda: asyncio.sleep(0)
        with mock.patch.object(tmux_bridge, "SPOOL", tempfile.mkdtemp()), contextlib.redirect_stdout(out):
            asyncio.run(bridge.pump_once())
        self.assertEqual(out.getvalue(), "", "a usual round says nothing")


if __name__ == "__main__":
    unittest.main()


class BrokenList(unittest.TestCase):
    def test_a_broken_machine_list_degrades_to_this_machine(self):
        from bot import remote
        a = remote.alt()

        def bad():
            raise a.ConfigError("machines.json 存在但讀不出來")
        with mock.patch.object(a, "inventory", bad), mock.patch.object(a, "this_machine", bad):
            self.assertEqual(remote.hosts(), {})
            self.assertTrue(remote.this_machine())
            self.assertIsNone(remote.machine_of("somewhere"))
            self.assertIn("讀不出來", remote.problem[1])
        self.assertEqual(remote.inventory() is not None, True)

    def test_pump_start_does_not_raise_with_a_broken_list(self):
        from bot import remote
        a = remote.alt()

        def bad():
            raise a.ConfigError("broken")
        bridge = tmux_bridge.TmuxBridge(Host())
        with mock.patch.object(a, "inventory", bad):
            bridge.start_streams()  # what pump() runs before its loop
        self.assertEqual(bridge.streaming, set())
