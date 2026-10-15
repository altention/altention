"""A version counts as good only once a hook event has gone hook → spool → pump (not merely logged in)."""
import json
import os
import subprocess
import tempfile
import unittest
from unittest import mock

from bot import events
from tests import tmuxguard  # noqa: F401,E402  (no test reaches a real tmux server)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class SelfTest(unittest.TestCase):
    def test_the_real_hook_spools_the_made_up_event(self):
        root = tempfile.mkdtemp()
        subprocess.run([os.path.join(ROOT, "tools", "dchook")], input=json.dumps({"hook_event_name": "SelfTest", "token": "t1"}),
                       universal_newlines=True, env=dict(os.environ, DCBOT_ROOT=root, DCBOT_CHANNEL="selftest"), check=True)
        (path, ev), = events.waiting(os.path.join(root, "state", "events"))
        self.assertEqual((ev["channel"], ev["hook_event_name"], ev["token"]), ("selftest", "SelfTest", "t1"))

    def test_ready_is_written_only_for_our_token(self):
        from bot import main
        state = tempfile.mkdtemp()
        bot = object.__new__(main.Bot)  # no Discord connection needed
        bot.selftest_token = "mine"
        with mock.patch.object(main, "STATE", state):
            self.assertFalse(bot.ready({"token": "someone else"}))
            self.assertFalse(os.path.exists(os.path.join(state, "ready")))
            self.assertTrue(bot.ready({"token": "mine"}))
            self.assertTrue(os.path.exists(os.path.join(state, "ready")))


if __name__ == "__main__":
    unittest.main()


class RestartWaits(unittest.TestCase):
    def test_a_restart_waits_for_half_done_work(self):
        from bot import main
        bot = object.__new__(main.Bot)
        bot.active = 0
        bot.posts = type("P", (), {"inflight": set()})()
        bot.tmux = type("T", (), {"handling": False})()
        self.assertFalse(bot.busy_with_work())
        bot.posts.inflight.add("/spool/1.json")
        self.assertTrue(bot.busy_with_work(), "a post being handed over")
        bot.posts.inflight.clear()
        bot.tmux.handling = True
        self.assertTrue(bot.busy_with_work(), "an event being posted")


class RestartFlag(unittest.TestCase):
    def test_a_flag_from_before_start_does_not_restart_again(self):
        import time as _time
        from bot import main
        path = os.path.join(tempfile.mkdtemp(), "restart-requested")
        open(path, "w").close()
        seen = main.mtime(path)
        self.assertFalse(main.fresh(path, seen), "the flag that started this process")
        os.utime(path, (seen + 10, seen + 10))
        self.assertTrue(main.fresh(path, seen), "a new one written since")
        self.assertTrue(main.fresh(path, None), "none at start, one now")
        self.assertFalse(main.fresh(path + ".none", None))
