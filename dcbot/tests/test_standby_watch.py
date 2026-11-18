"""C-163 d2b: the standby's watch (dclease watch, the core's standby-watch): the three cases, each said once."""
import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))))
from altention import testing as _testing  # noqa: E402
_testing.guard_home()  # tests never touch the real home (altention/testing.py; 11-08: one left a real dcremote-owner)
import json
import os
import tempfile
import unittest
from unittest import mock

from tests import tmuxguard  # noqa: F401  (no test reaches a real tmux server)
from tests.test_dclease import load_tool

MACHINES = {"host-a": {"roles": ["dcbot"], "ssh": ["a"]}, "host-b": {"roles": ["dcbot-standby"], "ssh": ["b"]}}


class Watch(unittest.TestCase):
    def setUp(self):
        self.tool = load_tool()
        self.state = tempfile.mkdtemp()
        p = mock.patch.object(self.tool, "WATCH", os.path.join(self.state, "standby-watch.json"))
        p.start()
        self.addCleanup(p.stop)
        self.posted, self.notified = [], []

    def run_watch(self, look, now, token=False, url="https://discord.com/api/webhooks/1/x", me="host-b", running=False):
        from altention import config as engine_config
        d = mock.Mock(notify=lambda text: self.notified.append(text))
        with mock.patch.object(self.tool.remote, "inventory", lambda: {"machines": MACHINES}), \
                mock.patch.object(self.tool.remote, "this_machine", lambda: me), \
                mock.patch.object(self.tool, "look", lambda primary: dict(look)), \
                mock.patch.object(self.tool, "webhook_url", lambda: url), \
                mock.patch.object(self.tool, "by_webhook", lambda u, text: self.posted.append((u, text))), \
                mock.patch.object(self.tool, "bot_here_running", lambda: running), \
                mock.patch.object(engine_config, "bot_token_there", lambda: token):
            return self.tool.watch(now=now, d_factory=lambda: d)

    UP = {"ok": True, "heartbeat": 30, "discord": 30, "up": True}

    def test_notify_only_the_bot_down_said_once(self):
        down = {"ok": True, "heartbeat": 400, "discord": 400, "up": False}
        self.assertEqual(self.run_watch(down, 1000), ["⛔ host-a 上的機器人停了超過 5 分鐘（host-b 發現的；沒有設定自動接手）"])
        self.assertEqual(self.run_watch(down, 1060), [], "once")
        self.run_watch(self.UP, 1120)
        self.assertEqual(len(self.run_watch(down, 1180)), 1, "back in between: said again")
        self.assertEqual([u for u, _ in self.posted], ["https://discord.com/api/webhooks/1/x"] * 2)
        self.assertEqual(self.notified, [], "no token: never by REST")

    def test_ssh_failing_five_minutes(self):
        self.assertEqual(self.run_watch({"ok": False}, 1000), [])
        self.assertEqual(self.run_watch({"ok": False}, 1200), [], "not five minutes yet")
        self.assertEqual(len(self.run_watch({"ok": False}, 1300)), 1)

    def test_up_but_not_reaching_discord(self):
        away = {"ok": True, "heartbeat": 900, "discord": 700, "up": True}
        self.assertEqual(self.run_watch(away, 1000), ["⚠️ host-a 上的機器人連不上 Discord 已經 10 分鐘，訊息暫時沒人回"],
                         "its process up: not called down")
        self.assertEqual(self.run_watch(away, 1060), [])
        self.assertEqual(self.run_watch(away, 1000, token=True, url=None), [], "said already (the same file of episodes)")

    def test_with_the_token_by_rest_and_no_down_notice(self):
        down = {"ok": True, "heartbeat": 400, "discord": 400, "up": False}
        self.assertEqual(self.run_watch(down, 1000, token=True, url=None), [], "the lease takes over: nothing to tell")
        away = {"ok": True, "heartbeat": 900, "discord": 700, "up": True}
        self.assertEqual(len(self.run_watch(away, 1100, token=True, url=None)), 1)
        self.assertEqual(len(self.notified), 1)
        self.assertEqual(self.posted, [])

    def test_the_primary_missing_from_the_lease_ten_minutes(self):
        """manager on 5f1addf: a standby whose gate has seen no message of the primary for 10 minutes says so once."""
        p = mock.patch.object(self.tool, "MISSING", os.path.join(self.state, "missing.json"))
        p.start()
        self.addCleanup(p.stop)
        self.tool.save(self.tool.MISSING, 1000.0)
        self.assertEqual(self.run_watch(self.UP, 1300, token=True, url=None), [], "5 minutes: not yet")
        said = self.run_watch(self.UP, 1700, token=True, url=None)
        self.assertEqual(said, ["⚠️ host-a 已經 11 分鐘沒在 #bot-lease 留下紀錄，備援沒辦法判斷它還在不在，所以 host-a 停了也不會接手。請跑 doctor。"])
        self.assertEqual(self.run_watch(self.UP, 1760, token=True, url=None), [], "once")
        os.remove(os.path.join(self.state, "missing.json"))
        self.run_watch(self.UP, 1820, token=True, url=None)
        self.tool.save(self.tool.MISSING, 2000.0)
        self.assertEqual(len(self.run_watch(self.UP, 2700, token=True, url=None)), 1, "back in between: said again")

    def test_nothing_to_do(self):
        down = {"ok": True, "heartbeat": 400, "discord": 400, "up": False}
        self.assertEqual(self.run_watch(down, 1000, url=None), [], "neither token nor webhook")
        self.assertEqual(self.run_watch(down, 1000, me="host-a"), [], "not a standby")
        self.assertEqual(self.run_watch(down, 1000, running=True), [], "this machine is the bot now")

    def test_only_a_discord_webhook_is_used(self):
        from altention import secret_store
        d = tempfile.mkdtemp()
        p = os.path.join(d, "w")
        for value, ok in (("https://discord.com/api/webhooks/1/x\n", True), ("https://example.com/hook", False)):
            with open(p, "w") as f:
                f.write(value)
            with mock.patch.object(secret_store, "path", lambda plugin, name: p):
                self.assertEqual(self.tool.webhook_url() is not None, ok)


class Troubles(unittest.TestCase):
    def test_said_once_a_kind(self):
        tool = load_tool()
        state = tempfile.mkdtemp()
        out = []
        with mock.patch.object(tool, "WATCH", os.path.join(state, "w.json")), mock.patch("builtins.print", out.append):
            for line in ("URLError: x", "URLError: x", "URLError: x", "HTTPError: 404", None, None):
                tool.said_trouble(line)
        self.assertEqual(out, ["dclease watch: URLError: x", "dclease watch: HTTPError: 404", "dclease watch: 沒有問題了"])

    def test_a_failed_post_keeps_what_was_seen(self):
        """#code-review on bb2f2f2 (manager): ssh_bad_since is not lost when the webhook fails."""
        w = Watch("test_ssh_failing_five_minutes")
        w.setUp()
        w.run_watch({"ok": False}, 1000)
        with mock.patch.object(w.tool, "by_webhook", side_effect=OSError("off the net")):
            def failing(*a, **k):
                raise OSError("off the net")
            from altention import config as engine_config
            with mock.patch.object(w.tool.remote, "inventory", lambda: {"machines": MACHINES}), \
                    mock.patch.object(w.tool.remote, "this_machine", lambda: "host-b"), \
                    mock.patch.object(w.tool, "look", lambda p: {"ok": False}), \
                    mock.patch.object(w.tool, "webhook_url", lambda: "https://discord.com/api/webhooks/1/x"), \
                    mock.patch.object(w.tool, "bot_here_running", lambda: False), \
                    mock.patch.object(engine_config, "bot_token_there", lambda: False):
                with self.assertRaises(OSError):
                    w.tool.watch(now=1400)
        self.assertEqual(w.run_watch({"ok": False}, 1460), ["⛔ host-a 上的機器人停了超過 5 分鐘（host-b 發現的；沒有設定自動接手）"],
                         "still five minutes since the first failure: said next minute")


class Look(unittest.TestCase):
    def test_parsed_by_the_primarys_clock(self):
        tool = load_tool()
        with mock.patch.object(tool, "ssh", lambda host, cmd: (0, "1000\n970\n400\nup\n")), \
                mock.patch.object(tool.remote, "engine_of", lambda h: "/e"):
            self.assertEqual(tool.look("host-a"), {"ok": True, "heartbeat": 30, "discord": 600, "up": True})
        with mock.patch.object(tool, "ssh", lambda host, cmd: (0, "1000\n\n\ndown\n")), \
                mock.patch.object(tool.remote, "engine_of", lambda h: "/e"):
            self.assertEqual(tool.look("host-a"), {"ok": True, "heartbeat": None, "discord": None, "up": False})
        with mock.patch.object(tool, "ssh", lambda host, cmd: (255, "")), mock.patch.object(tool.remote, "engine_of", lambda h: "/e"):
            self.assertEqual(tool.look("host-a"), {"ok": False})


class LookForReal(unittest.TestCase):
    """manager 11-08: a command for another machine's shell is run for real once (sh -c, a scratch HOME)."""

    def look_with(self, pid, end=""):
        import subprocess
        import time as _t
        tool = load_tool()
        home = tempfile.mkdtemp()
        state = os.path.join(home, "altention", "engine", "dcbot", "state")
        os.makedirs(state)
        for name, value in (("heartbeat", int(_t.time()) - 30), ("discord-ok", int(_t.time()) - 700), ("bot.pid", pid)):
            with open(os.path.join(state, name), "w") as f:
                f.write(str(value) + end)

        def real_ssh(host, cmd):
            r = subprocess.run(["sh", "-c", cmd], capture_output=True, text=True, timeout=30, env=dict(os.environ, HOME=home))
            return r.returncode, r.stdout
        with mock.patch.object(tool, "ssh", real_ssh), mock.patch.object(tool.remote, "engine_of", lambda h: "~/'altention/engine'"):
            return tool.look("host-a")

    def test_sh_c(self):
        import subprocess
        import sys
        bot = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)", "-m", "bot.main"])
        self.addCleanup(lambda: (bot.kill(), bot.wait()))
        got = self.look_with(bot.pid)
        self.assertTrue(got["ok"] and got["up"], got)
        self.assertTrue(25 <= got["heartbeat"] <= 60, got)
        self.assertTrue(695 <= got["discord"] <= 730, got)

    def test_files_that_end_with_a_newline(self):
        """manager 11-08 (found on the mac): a value written with a newline at its end reads the same."""
        import subprocess
        import sys
        bot = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)", "-m", "bot.main"])
        self.addCleanup(lambda: (bot.kill(), bot.wait()))
        got = self.look_with(bot.pid, end="\n")
        self.assertTrue(got["ok"] and got["up"], got)
        self.assertTrue(25 <= got["heartbeat"] <= 60 and 695 <= got["discord"] <= 730, got)

    def test_a_pid_given_to_another_program_is_no_bot(self):
        """#code-review-2 on bb2f2f2 (manager): after the primary restarted, its old bot.pid may be another program's."""
        got = self.look_with(os.getpid())
        self.assertTrue(got["ok"], got)
        self.assertFalse(got["up"], "alive, but not bot.main: down")


class Schedule(unittest.TestCase):
    def test_in_the_core_and_may_be_off(self):
        from altention import plugin
        here = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        with open(os.path.join(here, "altention", "core.plugin.json"), encoding="utf-8") as f:
            d = json.load(f)
        item = next(c for c in d["cron"] if c["name"] == "standby-watch")
        self.assertEqual(item["on"], "standby")
        self.assertIn("standby-watch", plugin.CORE_OVERRIDABLE)
        self.assertNotIn("standby-watch", plugin.CORE_ALWAYS_ON)


if __name__ == "__main__":
    unittest.main()


class DiscordOkWithoutALease(unittest.TestCase):
    """#code-review on bb2f2f2 (manager): with no lease (#bot-lease gone), the gateway itself says Discord is
    reached, so a standby's watch does not call this bot cut off."""

    def beat(self, lease, up, standby_on=True):
        import asyncio
        import types
        from bot import main
        b = types.SimpleNamespace(lease=lease, told=[], standby_on=standby_on)
        b.leader_ok = lambda: True
        b.gateway_up = lambda: up
        b.discord_ok = lambda: b.told.append(1)

        async def stop(secs):
            raise asyncio.CancelledError
        state = tempfile.mkdtemp()
        with mock.patch.object(main, "STATE", state), mock.patch.object(main.asyncio, "sleep", stop):
            try:
                asyncio.run(main.Bot.heartbeat(b))
            except asyncio.CancelledError:
                pass
        return b.told

    def test_written_by_the_gateway_when_there_is_no_lease(self):
        self.assertEqual(self.beat(None, True), [1])
        self.assertEqual(self.beat(None, False), [], "not connected: not said")
        self.assertEqual(self.beat(object(), True), [], "with a lease, its reads say it (as before)")
        self.assertEqual(self.beat(None, True, standby_on=False), [], "no standby at all: nothing changes")
