"""C-163 step 4a: what doctor asks of a standby (dclease check, standby.readiness) and the lows of the earlier batches."""
import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))))
from altention import testing as _testing  # noqa: E402
_testing.guard_home()  # tests never touch the real home (altention/testing.py; 11-08: one left a real dcremote-owner)
import asyncio
import json
import os
import subprocess
import sys
import tempfile
import time
import types
import unittest
from unittest import mock

from bot import main, remote, standby
from tests import tmuxguard  # noqa: F401  (no test reaches a real tmux server)
from tests.test_dclease import load_tool

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MACHINES = {"host-a": {"roles": ["dcbot"], "ssh": ["a"]}, "host-b": {"roles": ["dcbot-standby"], "ssh": ["b"]}}


class Readiness(unittest.TestCase):
    def write(self, **meta):
        d = tempfile.mkdtemp()
        with open(os.path.join(d, "meta.json"), "w") as f:
            json.dump(meta, f)
        return d

    def test_what_the_push_says(self):
        self.assertEqual(standby.readiness(tempfile.mkdtemp(), HERE), {"pushed": None})
        d = self.write(at=time.time() - 120, protocol=standby.PROTOCOL, requirements=standby.requirements(HERE),
                       token_hash="aaaa", settings={"MODEL": "x"})
        with mock.patch.object(standby, "token_hash", lambda name="discord-token": "aaaa"), \
                mock.patch.object(standby, "settings_now", lambda: {"MODEL": "y", "BRIDGE": "tmux"}):
            got = standby.readiness(d, HERE)
        self.assertTrue(110 <= got["pushed"] <= 200)
        self.assertEqual((got["protocol"], got["requirements"], got["token_same"]), (True, True, True))
        self.assertEqual(got["settings"], ["BRIDGE", "MODEL"])
        with mock.patch.object(standby, "token_hash", lambda name="discord-token": "bbbb"):
            self.assertFalse(standby.readiness(d, HERE)["token_same"], "only the hashes compared")
        with mock.patch.object(standby, "token_hash", lambda name="discord-token": None):
            self.assertIsNone(standby.readiness(d, HERE)["token_same"], "not known: not said to differ")


class HealthSame(unittest.TestCase):
    """#code-review on 5e2a4e0 (manager): the healthchecks address on the standby compared with the primary's (hashes)."""

    def test_compared_by_hash(self):
        d = tempfile.mkdtemp()

        def push(health):
            with open(os.path.join(d, "meta.json"), "w") as f:
                json.dump({"at": time.time(), "protocol": standby.PROTOCOL, "requirements": standby.requirements(HERE),
                           "health_hash": health}, f)
        for theirs, mine, want in (("aaaa", "aaaa", True), ("aaaa", "bbbb", False), ("aaaa", None, False),
                                   (None, "bbbb", False), (None, None, None)):
            push(theirs)
            with mock.patch.object(standby, "token_hash",
                                   lambda name="discord-token", mine=mine: mine if name == "healthchecks-url" else None):
                self.assertEqual(standby.readiness(d, HERE)["health_same"], want, (theirs, mine))


class ProxySaid(unittest.TestCase):
    def test_said_in_audit_when_set(self):
        from bot import messages
        self.assertIn("請把 DCBOT_PROXY 拿掉", messages.lease("proxy_on", proxy="127.0.0.1:9"))
        import inspect
        self.assertIn('messages.lease("proxy_on"', inspect.getsource(main.Bot.on_ready), "on_ready says it")

    def test_no_user_or_password_shown(self):
        """#code-review on e01a8c2: host:port only."""
        self.assertEqual(main.proxy_shown("http://lap:secret" + "@" + "127.0.0.1:8888"), "127.0.0.1:8888")
        self.assertEqual(main.proxy_shown("http://127.0.0.1:9"), "127.0.0.1:9")
        self.assertEqual(main.proxy_shown("nonsense"), "?")


class DrillFailback(unittest.TestCase):
    """manager 11-08: the failback wait shortened in the drill's rig alone."""

    def secs(self, home, test, value):
        with mock.patch.dict(os.environ, {"HOME": home, "ALTENTION_STANDBY_TEST": test, "DCBOT_DRILL_FAILBACK_SECS": value}):
            return main.drill_failback_secs()

    def test_only_in_the_rig(self):
        import pwd
        real, scratch = pwd.getpwuid(os.getuid()).pw_dir, tempfile.mkdtemp()
        self.assertEqual(self.secs(scratch, "1", "180"), 180)
        self.assertEqual(self.secs(real, "1", "180"), main.lease_mod.FAILBACK_SECS, "the real HOME: never")
        self.assertEqual(self.secs(scratch, "", "180"), main.lease_mod.FAILBACK_SECS, "no test switch: never")
        self.assertEqual(self.secs(scratch, "1", "5"), main.lease_mod.FAILBACK_SECS, "under a minute: ignored")
        self.assertEqual(self.secs(scratch, "1", "x"), main.lease_mod.FAILBACK_SECS)
        with mock.patch.dict(os.environ, {"HOME": scratch, "ALTENTION_STANDBY_TEST": "1", "DCBOT_DRILL_FAILBACK_SECS": "1800"}):
            self.assertTrue(main.drill_failback_taken(), "1800 is taken too (both reviews on 8fc5be4)")
        from altention import standby as alt
        with mock.patch.object(alt, "READY", True):
            self.assertEqual(self.secs(real, "", "180"), main.lease_mod.FAILBACK_SECS, "READY never opens it")

    def test_the_round_uses_it_and_says_it(self):
        import inspect
        from bot import messages
        self.assertIn("drill_failback_secs()", inspect.getsource(main.Bot.failback_due))
        self.assertIn("180 秒就切回", messages.lease("drill_failback_on", secs=180))
        self.assertIn("請把它拿掉", messages.lease("drill_failback_off"))


class DrillSender(unittest.TestCase):
    """manager 11-08: a webhook speaking for the owner, only in the drill's rig (the standby's test gate)."""

    def setUp(self):
        import pwd
        self.real = pwd.getpwuid(os.getuid()).pw_dir
        self.scratch = tempfile.mkdtemp()

    def sender(self, home, test, value="42"):
        env = {"HOME": home, "DCBOT_DRILL_SENDER": value, "ALTENTION_STANDBY_TEST": test}
        with mock.patch.dict(os.environ, env):
            return main.drill_sender()

    def test_only_in_the_rig(self):
        self.assertEqual(self.sender(self.scratch, "1"), 42)
        self.assertIsNone(self.sender(self.real, "1"), "the real HOME: never")
        self.assertIsNone(self.sender(self.scratch, ""), "no test switch: never")
        self.assertIsNone(self.sender(self.scratch, "1", value=""))
        self.assertIsNone(self.sender(self.scratch, "1", value="abc"))

    def test_not_opened_by_ready(self):
        """READY turned on one day opens the standby, never this."""
        from altention import standby as alt
        with mock.patch.object(alt, "READY", True):
            self.assertIsNone(self.sender(self.real, ""))

    def test_a_webhook_message_is_ignored_outside_the_rig(self):
        import asyncio
        bot = main.Bot.__new__(main.Bot)
        bot.audit = mock.AsyncMock()
        bot._connection = mock.Mock()
        seen = []
        msg = mock.Mock(webhook_id=42, author=mock.Mock(bot=True, id=42), mentions=[])
        with mock.patch.object(main, "drill_sender", lambda: None), \
                mock.patch.object(main.Bot, "user", None, create=True):
            bot.lease = mock.Mock(side_effect=AssertionError)
            asyncio.run(bot.on_message(msg))
        self.assertEqual(seen, [])
        self.assertFalse(bot.lease.called)

    def test_said_in_audit(self):
        import inspect
        from bot import messages
        src = inspect.getsource(main.Bot.on_ready)
        self.assertIn('"drill_sender_on" if drill_sender() else "drill_sender_off"', src)
        self.assertIn("請把它拿掉", messages.lease("drill_sender_off"))


class Proxy(unittest.TestCase):
    """The drill's DCBOT_PROXY: the gateway and REST through it; not set, nothing changes."""

    def test_only_when_set(self):
        with mock.patch.dict(os.environ, {"DCBOT_PROXY": ""}):
            self.assertEqual(sorted(main.client_options()), ["allowed_mentions"])
        with mock.patch.dict(os.environ, {"DCBOT_PROXY": "http://127.0.0.1:9"}):
            self.assertEqual(main.client_options()["proxy"], "http://127.0.0.1:9")
            import discord
            client = discord.Client(intents=discord.Intents.default(), **main.client_options())
            self.assertEqual(client.http.proxy, "http://127.0.0.1:9", "discord.py takes it for REST and the gateway")


class Check(unittest.TestCase):
    def setUp(self):
        self.tool = load_tool()
        self.state = tempfile.mkdtemp()
        for name, value in (("ROLE_FILE", os.path.join(self.state, "lease-role")), ("STANDBY_DIR", self.state)):
            p = mock.patch.object(self.tool, name, value)
            p.start()
            self.addCleanup(p.stop)

    def run_check(self, me, d=None, ssh_code=0):
        with mock.patch.object(self.tool.remote, "inventory", lambda: {"machines": MACHINES}), \
                mock.patch.object(self.tool.remote, "this_machine", lambda: me), \
                mock.patch.object(self.tool, "ssh", lambda host, cmd: (ssh_code, "")):
            import altention.secret_store as ss
            with mock.patch.object(ss, "fingerprint", lambda p, n: {"mode": 0o600, "hash": "aaaa"}):
                return self.tool.check(d)

    def test_the_primary_only_its_token(self):
        got = self.run_check("host-a")
        self.assertEqual((got["role"], got["token"]), ("primary", {"mode_ok": True}))
        self.assertNotIn("lease", got)

    def test_a_standby(self):
        p = mock.patch.multiple(self.tool.config, GUILD_ID=1, OWNER_ID=7)  # (a standby set up: both there)
        p.start()
        self.addCleanup(p.stop)
        d = types.SimpleNamespace(channel="9", messages=lambda: [])
        got = self.run_check("host-b", d)
        self.assertEqual((got["role"], got["lease"], got["ssh_primary"], got["pushed"]), ("standby", "ok", True, None))
        got = self.run_check("host-b", types.SimpleNamespace(channel=None, messages=lambda: []), ssh_code=255)
        self.assertEqual((got["lease"], got["ssh_primary"]), ("no-channel", False))
        self.assertNotIn("aaaa", json.dumps(got), "no hash of this machine printed either")

    def test_the_ids_missing_are_said(self):
        """Go-live 11-11 (manager): the standby without guild-id had "setup: 頻道不在"; now it says what is missing,
        without asking Discord in no server."""
        d = mock.Mock(side_effect=AssertionError("no lease read without a server"))
        with mock.patch.object(self.tool.config, "GUILD_ID", 0), mock.patch.object(self.tool.config, "OWNER_ID", 0):
            got = self.run_check("host-b", d)
        self.assertEqual((got["lease"], got["missing"]), ("missing", ["guild-id", "owner-id"]))
        with mock.patch.object(self.tool.config, "GUILD_ID", 1), mock.patch.object(self.tool.config, "OWNER_ID", 0):
            got = self.run_check("host-b", types.SimpleNamespace(channel="9", messages=lambda: []))
        self.assertEqual((got["lease"], got["missing"]), ("ok", ["owner-id"]), "the server known: read, owner-id said")

    def test_which_kind_of_standby(self):
        """auto: the bot's token there; notify: only the webhook (no lease read: no token to read it with)."""
        import altention.secret_store as ss
        prints = {"discord-token": None, "standby-webhook": {"mode": 0o600, "hash": "h"}}
        with mock.patch.object(self.tool.remote, "inventory", lambda: {"machines": MACHINES}), \
                mock.patch.object(self.tool.remote, "this_machine", lambda: "host-b"), \
                mock.patch.object(self.tool, "ssh", lambda host, cmd: (0, "")), \
                mock.patch.object(self.tool, "webhook_url", lambda: "https://discord.com/api/webhooks/1/x"), \
                mock.patch.object(self.tool, "Discord", side_effect=AssertionError("no lease read without a token")), \
                mock.patch.object(ss, "fingerprint", lambda p, n: prints[n]):
            got = self.tool.check()
        self.assertEqual((got["mode"], got["webhook"], got["ssh_primary"]), ("notify", {"mode_ok": True, "discord": True}, True))
        self.assertIn(got["missing"], ([], ["owner-id"]), "owner-id looked at for a notify standby too")
        with mock.patch.object(self.tool.remote, "inventory", lambda: {"machines": MACHINES}), \
                mock.patch.object(self.tool.remote, "this_machine", lambda: "host-b"), \
                mock.patch.object(self.tool, "ssh", lambda host, cmd: (0, "")), \
                mock.patch.object(self.tool, "webhook_url", lambda: "https://discord.com/api/webhooks/1/x"), \
                mock.patch.object(ss, "fingerprint", lambda p, n: prints[n]), \
                mock.patch.object(self.tool.config, "OWNER_ID", 0):
            self.assertEqual(self.tool.check()["missing"], ["owner-id"], "#code-review on b6fb3f9")
        self.assertEqual(self.run_check("host-b", types.SimpleNamespace(channel="9", messages=lambda: []))["mode"], "auto")

    def test_the_command_line(self):
        r = subprocess.run([sys.executable, os.path.join(HERE, "tools", "dclease"), "--help"], capture_output=True, text=True, timeout=60)
        self.assertIn("dclease check", r.stdout)


class Lows(unittest.TestCase):
    def test_owner_clear_refused_while_there_is_a_standby(self):
        home = tempfile.mkdtemp()
        inst = os.path.join(home, "inst")
        os.makedirs(inst)
        with open(os.path.join(inst, "machines.json"), "w") as f:
            json.dump({"machines": MACHINES}, f)
        tool = os.path.join(HERE, "tools", "dcremote")
        env = dict(os.environ, HOME=home, ALT_INSTANCE=inst)
        subprocess.run([sys.executable, tool, "--epoch", "3", "bot-host", "host-b", "3"], env=env, timeout=60)
        r = subprocess.run([sys.executable, tool, "owner-clear"], env=env, capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 2)
        self.assertTrue(json.loads(r.stdout)["error"].startswith("這邊還開著備援，不能清。"))
        self.assertTrue(os.path.exists(os.path.join(home, ".local", "share", "altention", "dcremote-owner")))

    def test_owner_clear(self):
        home = tempfile.mkdtemp()
        tool = os.path.join(HERE, "tools", "dcremote")
        env = dict(os.environ, HOME=home)
        env.pop("ALT_INSTANCE", None)
        subprocess.run([sys.executable, tool, "--epoch", "3", "bot-host", "host-b", "3"], env=env, timeout=60)
        self.assertEqual(subprocess.run([sys.executable, tool, "bind", "x", "y"], env=env, capture_output=True, timeout=60).returncode, 75)
        r = subprocess.run([sys.executable, tool, "owner-clear"], env=env, capture_output=True, text=True, timeout=60)
        self.assertEqual(json.loads(r.stdout), {"cleared": True})
        self.assertNotEqual(subprocess.run([sys.executable, tool, "bind", "x", "y"], env=env, capture_output=True, timeout=60).returncode,
                            75, "nothing remembered: as without a standby")

    def test_a_refusal_without_an_epoch_is_said(self):
        told = []

        async def once(host, alias, command, input, timeout):
            return 75, "", ""
        with mock.patch.object(remote, "EPOCH", None), mock.patch.object(remote, "on_stale", told.append), \
                mock.patch.object(remote, "_once", once), mock.patch.object(remote, "reach", mock.AsyncMock(return_value="a")), \
                mock.patch.dict(remote.LAST_GOOD, {}, clear=True):
            asyncio.run(remote.run("host-c", "true"))
        self.assertEqual(told, ["host-c"])

    def test_said_once_a_machine(self):
        b = types.SimpleNamespace(audits=[])

        async def audit(t):
            b.audits.append(t)
        b.audit = audit
        for host in ("host-c", "host-c", "host-d"):
            asyncio.run(main.Bot.stale_owner(b, host))
        self.assertEqual(len(b.audits), 2)
        self.assertIn("請跑 doctor", b.audits[0])

    def test_nothing_sent_before_the_first_round(self):
        """#code-review-2 on f68f24b (manager): with a standby, a request waits for the bot's epoch (its first lease
        round); past its time, nothing is sent."""
        sent = []

        async def once(host, alias, command, input, timeout):
            sent.append(command)
            return 0, "ok", ""

        async def go(set_after):
            async def later():
                await asyncio.sleep(set_after)
                remote.EPOCH = 4
            with mock.patch.object(remote, "LEASED", True), mock.patch.object(remote, "EPOCH", None), \
                    mock.patch.object(remote, "_once", once), mock.patch.object(remote, "reach", mock.AsyncMock(return_value="a")), \
                    mock.patch.dict(remote.LAST_GOOD, {}, clear=True):
                task = asyncio.ensure_future(later())
                got = await remote.run("host-c", "true", timeout=2)
                task.cancel()
                return got
        self.assertEqual(asyncio.run(go(0.6))[0], 0)
        self.assertEqual(len(sent), 1, "sent once the epoch is there")
        err_got = asyncio.run(go(5))
        self.assertEqual(err_got, (255, ""), "no epoch in its time: not sent")
        self.assertEqual(len(sent), 1)

    def test_check_has_a_time_limit(self):
        tool = load_tool()

        def slow(d=None, known=None):
            known["role"] = "standby"
            time.sleep(5)
        out = []
        with mock.patch.object(tool, "check", slow), mock.patch.object(tool, "CHECK_SECS", 1), \
                mock.patch("builtins.print", lambda s: out.append(s)):
            self.assertEqual(tool.main(["check"]), 0)
        self.assertEqual(json.loads(out[0]), {"role": "standby", "timed_out": 1}, "what is known, and that time ran out")

    def test_claim_drops_the_oldest(self):
        b = types.SimpleNamespace()
        for i in range(501):
            main.Bot.claim_turn(b, "5", "%08d" % i)
        self.assertTrue(main.Bot.claim_turn(b, "5", "%08d" % 0), "the oldest dropped: free again")
        self.assertFalse(main.Bot.claim_turn(b, "5", "%08d" % 500), "the newest kept")


if __name__ == "__main__":
    unittest.main()
