"""pump(): every channel's output goes through it, so nothing may end it."""
import asyncio
import json
import os
import tempfile
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
