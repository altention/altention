"""C-163 step 3a: another machine listens to the newest bot only (design section 1.4, 4.4): dcremote's epoch, the
shell check on what goes to tmux by ssh, the stream of an older bot, the bot-host override. Without an epoch (no
standby) nothing of it is checked or written."""
import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))))
from altention import testing as _testing  # noqa: E402
_testing.guard_home()  # tests never touch the real home (altention/testing.py; 11-08: one left a real dcremote-owner)
import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from bot import lease, remote
from tests import tmuxguard  # noqa: F401  (no test reaches a real tmux server)

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOL = os.path.join(HERE, "tools", "dcremote")


class Dcremote(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp()

    def run_tool(self, *args):
        env = dict(os.environ, HOME=self.home)
        return subprocess.run([sys.executable, TOOL] + list(args), env=env, capture_output=True, text=True, timeout=60)

    def owner(self):
        with open(os.path.join(self.home, ".local", "share", "altention", "dcremote-owner")) as f:
            return int(f.read())

    def bot_host(self):
        with open(os.path.join(self.home, ".local", "share", "altention", "bot-host")) as f:
            return json.load(f)

    def test_the_newest_epoch_is_kept_an_older_one_refused(self):
        r = self.run_tool("--epoch", "3", "bot-host", "host-b", "3")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual((self.owner(), self.bot_host()), (3, {"machine": "host-b", "epoch": 3}))
        r = self.run_tool("--epoch", "2", "bot-host", "host-a", "2")
        self.assertEqual(r.returncode, 75)
        self.assertEqual(self.bot_host()["machine"], "host-b", "not written by the older bot")
        r = self.run_tool("--epoch", "4", "bot-host", "host-a", "4")
        self.assertEqual((r.returncode, self.owner()), (0, 4))

    def test_an_older_bot_host_is_not_written_even_without_an_epoch(self):
        self.run_tool("--epoch", "3", "bot-host", "host-b", "3")
        self.assertEqual(self.run_tool("bot-host", "host-a", "1").returncode, 75)

    def test_without_an_epoch_nothing_is_checked_or_written(self):
        self.run_tool("bot-host", "host-a", "0")
        self.assertFalse(os.path.exists(os.path.join(self.home, ".local", "share", "altention", "dcremote-owner")))

    def test_once_a_bot_with_a_standby_came_no_epoch_is_old(self):
        """#code-review on abc07b3 (manager): a bot's request without an epoch (before its first lease round) is
        refused once this machine has seen one with an epoch; the stream does not start and stops nobody's. The
        altention command line's own requests (tmux-list…) are no bot's and still go."""
        self.run_tool("--epoch", "3", "bot-host", "host-b", "3")
        state = os.path.join(HERE, "state")
        pidfile = os.path.join(state, "stream.pid")
        before = open(pidfile).read() if os.path.exists(pidfile) else None
        r = self.run_tool("stream")
        self.assertEqual(r.returncode, 75)
        self.assertEqual(open(pidfile).read() if os.path.exists(pidfile) else None, before, "no pid file touched")
        self.assertEqual(self.run_tool("bind", "x", "y").returncode, 75)
        self.assertNotEqual(self.run_tool("tmux-list").returncode, 75, "the command line's: not refused for an epoch")

    def test_an_older_bots_stream_does_not_start(self):
        self.run_tool("--epoch", "3", "bot-host", "host-b", "3")
        r = self.run_tool("--epoch", "2", "stream")
        self.assertEqual(r.returncode, 75)
        self.assertIn("older", r.stdout)


class NewEpoch(unittest.TestCase):
    """#code-review-2 on 94f58e5: the primary back after the standby went away (not by failing back) is a new epoch,
    so machines that saw the standby's let it through."""

    def test_the_primary_back_takes_a_new_epoch(self):
        from tests import test_dclease as td
        g = td.Gate("test_the_primary_starts_when_nobody_holds_it")
        g.setUp()
        try:
            g.api.put("host-b", 1, True)  # the standby was the bot (epoch 1), then went away
            g.api.put("host-a", 0, False)
            g.api.tick(lease.FRESH_SECS + 60)
            self.assertEqual(g.gate("host-a"), g.tool.START)
            self.assertEqual(g.own("host-a")["epoch"], 3, "bigger than 1, and the primary's place (last of two)")
        finally:
            g.doCleanups()
        home = tempfile.mkdtemp()
        folder = os.path.join(home, ".local", "share", "altention")
        os.makedirs(folder)
        with open(os.path.join(folder, "dcremote-owner"), "w") as f:
            f.write("1")
        r = subprocess.run([sys.executable, TOOL, "--epoch", "3", "bot-host", "host-a", "3"], env=dict(os.environ, HOME=home),
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, "the machine that saw the standby's epoch 1 lets epoch 3 through")

    def test_a_holder_restarting_keeps_its_epoch(self):
        from tests import test_dclease as td
        g = td.Gate("test_the_primary_starts_when_nobody_holds_it")
        g.setUp()
        try:
            g.api.put("host-a", 3, True)
            g.api.tick(30)
            self.assertEqual(g.gate("host-a"), g.tool.START)
            self.assertEqual(g.own("host-a")["epoch"], 3, "its own fresh place: not a new one")
        finally:
            g.doCleanups()


class Shell(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp()
        folder = os.path.join(self.home, ".local", "share", "altention")
        os.makedirs(folder)
        with open(os.path.join(folder, "dcremote-owner"), "w") as f:
            f.write("5")

    def sh(self, epoch):
        with mock.patch.object(remote, "EPOCH", epoch):
            cmd = remote.fenced_shell("echo ran")
        return subprocess.run(["sh", "-c", cmd], env=dict(os.environ, HOME=self.home), capture_output=True, text=True, timeout=30)

    def test_tmux_by_ssh_is_fenced_too(self):
        self.assertEqual(self.sh(4).returncode, 75, "an older bot's keystrokes refused there")
        r = self.sh(5)
        self.assertEqual((r.returncode, r.stdout.strip()), (0, "ran"))

    def test_an_owner_file_that_is_no_number_is_zero(self):
        with open(os.path.join(self.home, ".local", "share", "altention", "dcremote-owner"), "w") as f:
            f.write("garbage")
        self.assertEqual(self.sh(1).returncode, 0, "as dcremote's owner() reads it")

    def test_without_an_epoch_unchanged(self):
        with mock.patch.object(remote, "EPOCH", None):
            self.assertEqual(remote.fenced_shell("tmux ls"), "tmux ls")
            self.assertNotIn("--epoch", remote.dcremote_command("host-b"))
        with mock.patch.object(remote, "EPOCH", 7), mock.patch.object(remote, "engine_of", lambda h: "/e"):
            self.assertTrue(remote.dcremote_command("host-b").endswith("dcremote --epoch 7"))
            self.assertIn("dcremote --epoch 7 stream", remote.stream_command("host-b"))


class PumpWaits(unittest.TestCase):
    def test_nothing_reaches_other_machines_before_this_one_is_the_bot(self):
        """#code-review on abc07b3: pump()'s start (streams, windows looked at, what was left posted) waits for
        leader_ok() when there is a standby."""
        import asyncio
        from bot import tmux_bridge
        bridge = tmux_bridge.TmuxBridge.__new__(tmux_bridge.TmuxBridge)
        state = {"ok": False}
        bridge.host = mock.Mock(lease=object(), leader_ok=lambda: state["ok"], channels_state={})
        bridge.read_words = mock.AsyncMock()
        bridge.remember_pids = mock.AsyncMock()
        bridge.start_streams = mock.Mock()
        bridge.unsent = mock.Mock()
        bridge.read_rewriter = mock.AsyncMock()
        bridge.posted_left = mock.AsyncMock()
        bridge.pump_once = mock.AsyncMock(side_effect=asyncio.CancelledError)

        async def go():
            with mock.patch.object(tmux_bridge, "write_settings", lambda: None):
                task = asyncio.ensure_future(bridge.pump())
                await asyncio.sleep(1.5)
                self.assertFalse(bridge.start_streams.called, "no stream before this machine is the bot")
                self.assertFalse(bridge.unsent.kick_all.called)
                state["ok"] = True
                try:
                    await asyncio.wait_for(task, 5)
                except asyncio.CancelledError:
                    pass
            self.assertTrue(bridge.start_streams.called)
        asyncio.run(go())


class BotHost(unittest.TestCase):
    def test_bot_machine_follows_the_override(self):
        from altention import config
        home = tempfile.mkdtemp()
        machines = {"machines": {"host-a": {"roles": ["dcbot"], "ssh": ["a"]}, "host-b": {"roles": ["dcbot-standby"], "ssh": ["b"]}}}
        with mock.patch.dict(os.environ, {"HOME": home}), mock.patch.object(config, "inventory", lambda: machines), \
                mock.patch.object(config, "this_machine", lambda: "host-c"), mock.patch.object(config, "known_here", lambda: True):
            self.assertEqual(config.bot_machine()[0], "host-a")
            os.makedirs(os.path.dirname(config.bot_host_path()))
            with open(config.bot_host_path(), "w") as f:
                json.dump({"machine": "host-b", "epoch": 2}, f)
            name, entry, here = config.bot_machine()
            self.assertEqual((name, entry["ssh"], here), ("host-b", ["b"], False))
            with open(config.bot_host_path(), "w") as f:
                json.dump({"machine": "host-x", "epoch": 3}, f)
            self.assertEqual(config.bot_machine()[0], "host-a", "a machine the list does not know: the list as always")


class Bot(unittest.TestCase):
    def test_refused_as_older_gives_the_place_up(self):
        from bot import main
        b = mock.Mock()
        b.lease_over = False
        b.lease.epoch = 2
        made = []
        b.loop.create_task = lambda coro: made.append(coro) or coro.close()
        main.Bot.old_bot(b, "host-b")
        self.assertEqual(len(made), 2, "#audit and the place given up")
        b.lease_over = True
        main.Bot.old_bot(b, "host-b")
        self.assertEqual(len(made), 2, "once")


if __name__ == "__main__":
    unittest.main()
