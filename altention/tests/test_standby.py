"""altention.standby (C-163 d3a): a standby set up from the bot's machine. The token and the webhook address go to
the other machine on ssh's stdin only, never in a command line; run for real (a stand-in ssh that runs the command
with sh -c in a scratch HOME, the engine's own `altention secret set`), as the manager asked for every command sent
to another machine's shell. In a throwaway home; #audit never really posted to."""
import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))))
from altention import testing as _testing  # noqa: E402
_testing.guard_home()  # tests never touch the real home (altention/testing.py)
import io
import json
import os
import stat
import tempfile
import unittest
from unittest import mock

from altention import config, secret_store, standby

ENGINE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
VALUE = "Rig.TOKEN.value-never-on-a-command-line"


class Rig(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp()
        self.inst = os.path.join(self.home, "inst")
        os.makedirs(self.inst)
        self.machines = {"machines": {"host-a": {"ssh": ["local"], "roles": ["dcbot"]},
                                      "host-b": {"ssh": ["b"], "roles": [], "engine": ENGINE},
                                      "host-c": {"ssh": ["local"], "roles": []}}}
        with open(os.path.join(self.inst, "machines.json"), "w") as f:
            json.dump(self.machines, f)
        for p in (mock.patch.object(config, "inventory", lambda: json.loads(json.dumps(self.machines))),
                  mock.patch.object(config, "bot_machine", lambda: ("host-a", None, True))):
            p.start()
            self.addCleanup(p.stop)


class Candidates(Rig):
    def test_not_the_bot_and_reached_by_ssh(self):
        self.assertEqual(standby.candidates(), [("host-b", False)])


class Names(Rig):
    def test_the_bots_own_names(self):
        """#code-review-2 on 3a4e6b1: #bot-lease and the system category named as the bot reads them."""
        _sys.path.insert(0, os.path.join(ENGINE, "dcbot"))
        from bot import config as bot_config
        with mock.patch.object(bot_config, "LEASE_CHANNEL", "lease-x"), mock.patch.object(bot_config, "SYSTEM_CATEGORY", "sys-x"):
            self.assertEqual(standby.bot_channel_names(), ("lease-x", "sys-x"))


class Lock(Rig):
    def test_a_failed_commit_puts_the_file_back(self):
        import subprocess
        subprocess.run(["git", "-C", self.inst, "init", "-q"], check=True)
        with open(os.path.join(self.inst, "machines.json")) as f:
            before = f.read()
        with mock.patch.object(config, "instance_dir", lambda: self.inst):  # (no commit yet, no user: commit refused?)
            real = subprocess.run

            def no_commit(argv, **kw):
                if "commit" in argv:
                    return subprocess.CompletedProcess(argv, 1, "", "index.lock held")
                return real(argv, **kw)
            with mock.patch.object(standby.subprocess, "run", no_commit):
                with self.assertRaises(standby.Failed):
                    standby.add_role("host-b")
        with open(os.path.join(self.inst, "machines.json")) as f:
            self.assertEqual(f.read(), before, "said failed: not added after all")


class OnStdinOnly(Rig):
    """The value on ssh's stdin, never in its argv (what `ps` shows); stored there by the engine's own command."""

    def test_for_real(self):
        there = tempfile.mkdtemp()  # the other machine's HOME
        log = os.path.join(there, "argv.json")
        fake = os.path.join(there, "ssh")
        with open(fake, "w") as f:  # a stand-in ssh: keeps its argv, runs the last word with sh -c in that HOME
            f.write('#!/bin/sh\npython3 -c \'import json,sys; json.dump(sys.argv[1:], open(sys.argv[1],"w"))\' %s "$@"\n'
                    'eval "last=\\${$#}"\nHOME=%s https_proxy=http://127.0.0.1:1 http_proxy=http://127.0.0.1:1 '
                    'ALT_INSTANCE=%s exec sh -c "$last"\n' % (log, there, self.inst))
        os.chmod(fake, 0o755)
        with mock.patch.object(standby, "SSH", [fake]):
            standby.send_secret("host-b", "core", "discord-token", VALUE)
        with open(log) as f:
            argv = json.load(f)
        self.assertNotIn(VALUE, " ".join(argv), "never on the command line")
        self.assertIn("secret set core discord-token --stdin --via setup", argv[-1])
        stored = os.path.join(there, ".config", "altention", "secrets", "core", "discord-token")
        with open(stored) as f:
            self.assertEqual(f.read(), VALUE)
        self.assertEqual(stat.S_IMODE(os.stat(stored).st_mode), 0o600)

    def test_not_stored_is_said(self):
        with mock.patch.object(standby, "run_there", lambda m, c, v: (False, "Permission denied")):
            with self.assertRaises(standby.Failed) as e:
                standby.send_secret("host-b", "core", "discord-token", VALUE)
        self.assertNotIn(VALUE, str(e.exception))


class Setup(Rig):
    def setUp(self):
        super().setUp()
        p = mock.patch.object(standby, "READY", True)
        p.start()
        self.addCleanup(p.stop)

    def run_setup(self, mode, token=VALUE, applied=(True, True), sent=True, ping=None, ids=None):
        ids = {"guild-id": "100000000000000001", "owner-id": "100000000000000042"} if ids is None else ids
        done = self.done = []
        applies = list(applied)

        def send(m, p, n, v):
            done.append(("secret", m, n, v == VALUE or v == "https://discord.com/api/webhooks/1/x"))
            if not sent:
                raise standby.Failed("存不到 host-b 上（Permission denied）")
        steps = {"add_role": lambda m: done.append(("role", m)),
                 "send_secret": send,
                 "make_webhook": lambda t: done.append(("webhook",)) or "https://discord.com/api/webhooks/1/x",
                 "test_webhook": lambda u, p, m: done.append(("test", u)),
                 "apply": lambda m: (done.append(("apply", m)) or (applies.pop(0), ["ok"])),
                 "make_lease_channel": lambda t: done.append(("lease",)) or True,
                 "restart_bot": lambda: done.append(("restart",))}
        out, audit = io.StringIO(), []
        with mock.patch.object(standby, "read_secret", lambda p, n: token if n == "discord-token" else ping if n == "healthchecks-url" else ids.get(n)), \
                mock.patch.object(secret_store, "tell_audit", audit.append):
            code = standby.setup("host-b", mode, out, steps)
        return code, done, out.getvalue(), audit

    def test_the_ids_go_along_and_none_missing(self):
        """Go-live 11-11 (manager): the standby had the token but not guild-id and owner-id: its bot read #bot-lease
        in no server. Both go along in either mode, before the role; one missing here: nothing done at all."""
        for mode in ("auto", "notify"):
            code, done, out, audit = self.run_setup(mode)
            self.assertEqual([d[2] for d in done if d[0] == "secret"][-2:], ["guild-id", "owner-id"], mode)
            self.assertLess([d[0] for d in done].index("role"), len(done))
            self.assertLess(max(i for i, d in enumerate(done) if d[0] == "secret"), [d[0] for d in done].index("role"))
            self.assertIn("✓ 伺服器 ID 和擁有者 ID 也放到了 host-b", out)
            self.assertNotIn("100000000000000001", out + " ".join(audit), "values never said")
        with self.assertRaises(standby.Failed) as e:
            self.run_setup("auto", ids={"guild-id": "100000000000000001"})
        self.assertEqual(str(e.exception), "這台沒有存機器人的擁有者 ID（owner-id），沒辦法交給備援；先在這台跑 python3 ops/doctor.py，照它說的補上")
        self.assertEqual(self.done, [], "nothing done: no apply, no secret, no role")

    def test_apply_ending_with_a_todo_is_an_update_all_the_same(self):
        """The drill's item 23 (manager 11-10): apply's 2 (done, something left for a person: a git user not set)
        stopped the setup as "not updated". Only 0 and 2 go on; 1 and 3 stop as before."""
        for rc, ok in ((0, True), (2, True), (1, False), (3, False)):
            with mock.patch.object(standby.subprocess, "run", lambda *a, rc=rc, **k: mock.Mock(returncode=rc, stdout="x\nlast line\n", stderr="")):
                got = standby.apply("host-b")
            self.assertEqual(got[0], ok, rc)
            self.assertEqual(got[1], [standby.TODO_MARK] if rc == 2 else ["last line"], rc)
        code, done, out, audit = self.run_setup("auto", applied=(True, True))
        self.assertNotIn("手動做的事", out)
        self.assertIn("加上了 dcbot-standby 角色", out)
        steps_applied = [(True, [standby.TODO_MARK]), (True, [standby.TODO_MARK])]
        self.done = []
        with mock.patch.object(standby, "read_secret", lambda p, n: VALUE if n == "discord-token" else "100000000000000001" if n in standby.IDS else None), \
                mock.patch.object(secret_store, "tell_audit", lambda t: None):
            buf = io.StringIO()
            code = standby.setup("host-b", "auto", buf, {
                "add_role": lambda m: False, "send_secret": lambda *a: None, "make_lease_channel": lambda t: True,
                "restart_bot": lambda: None, "apply": lambda m: steps_applied.pop(0)})
        self.assertEqual(code, 0)
        self.assertIn("✓ host-b 照新設定裝好了，但那台還有幾件要你手動做的事；請在那台的 ~/altention/engine 跑", buf.getvalue())
        self.assertIn("✓ host-b 本來就有 dcbot-standby 角色，機器清單沒有改", buf.getvalue(), "the role there already: said so")
        self.assertIn("設好了；機器人正在重啟", buf.getvalue())

    def test_auto_in_the_manager_order(self):
        code, done, out, audit = self.run_setup("auto")
        self.assertEqual(code, 0)
        self.assertEqual([d[0] for d in done], ["apply", "secret", "secret", "secret", "role", "apply", "lease", "restart"],
                         "updated, the secret, only then the role, apply, the channel, the restart (manager on 2956b55)")
        self.assertEqual(done[1], ("secret", "host-b", "discord-token", True))
        self.assertNotIn(VALUE, out + " ".join(audit), "never printed nor in #audit")
        self.assertIn("自動接手", audit[0])
        self.assertTrue(out.rstrip().endswith("設好了；機器人正在重啟，重啟後就開始把狀態傳給 host-b。"))

    def test_the_outside_notice_goes_along(self):
        """manager on e2e60ab: whichever machine is the bot pings healthchecks, so its address goes to the standby too
        (auto: it may be the bot), by the same stdin; notify: it never is, nothing more sent."""
        code, done, out, _ = self.run_setup("auto", ping="https://hc-ping.com/abc")
        self.assertEqual([d[2] for d in done if d[0] == "secret"], ["discord-token", "healthchecks-url", "guild-id", "owner-id"])
        self.assertNotIn("hc-ping.com/abc", out)
        _, done, _, _ = self.run_setup("notify", ping="https://hc-ping.com/abc")
        self.assertEqual([d[2] for d in done if d[0] == "secret"], ["standby-webhook", "guild-id", "owner-id"])  # (no token, no healthchecks address: never the bot)
        _, done, _, _ = self.run_setup("auto")
        self.assertEqual([d[2] for d in done if d[0] == "secret"], ["discord-token", "guild-id", "owner-id"], "no healthchecks address: none sent")

    def test_notify(self):
        code, done, out, audit = self.run_setup("notify")
        self.assertEqual([d[0] for d in done], ["apply", "webhook", "secret", "test", "secret", "secret", "role", "apply", "lease", "restart"])
        self.assertEqual(done[2][2], "standby-webhook")
        self.assertNotIn("webhooks/1/x", out + " ".join(audit), "the address never printed")

    def test_a_secret_not_handed_over_adds_no_role(self):
        with self.assertRaises(standby.Failed):
            self.run_setup("auto", sent=False)
        self.assertEqual([d[0] for d in self.done], ["apply", "secret"], "no role, no restart (manager on 2956b55)")

    def test_refused(self):
        with self.assertRaises(standby.Failed):
            self.run_setup("auto", token=None)
        with self.assertRaises(standby.Failed):
            standby.setup("host-a", "auto", io.StringIO(), {})
        with self.assertRaises(standby.Failed) as e:
            self.run_setup("auto", applied=(False,))
        self.assertIn("什麼都還沒動", str(e.exception), "its engine not updated: nothing done")
        code, _, out, _ = self.run_setup("auto", applied=(True, False))
        self.assertEqual(code, 1)
        self.assertIn("ops/apply.py host-b", out)


class Switch(Rig):
    """The manager's switch: READY False until the drill (11-08), on since it passed (11-11)."""

    def test_on(self):
        """The drill passed (manager 11-11): on in the engine, without the rig's variable."""
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop(standby.TEST_VAR, None)
            self.assertTrue(standby.READY)
            self.assertTrue(standby.ready())

    def test_off_again_refuses_as_before(self):
        """Turned off one day (READY False): setup and status refuse with the words they had."""
        with mock.patch.dict(os.environ, {}, clear=False), mock.patch.object(standby, "READY", False):
            os.environ.pop(standby.TEST_VAR, None)
            self.assertFalse(standby.ready())
            with self.assertRaises(standby.Failed) as e:
                standby.setup("host-b", "auto", io.StringIO(), {})
            self.assertEqual(str(e.exception), "備援功能還在測試，這一版還不能設。")
            out = io.StringIO()
            with mock.patch("sys.stdout", out):
                self.assertEqual(standby.main(["setup", "host-b", "auto"]), 1)
                self.assertEqual(standby.main(["status"]), 1)
            self.assertIn("備援功能還在測試，這一版還不能設。", out.getvalue())

    def test_off_again_the_rig_still_can(self):
        """READY False one day: the drill's rig (its variable, a scratch HOME) can still set one up (#code-review-2 on
        d9ebedf: nothing covered ready()'s `or drill_rig()` once READY was on)."""
        with mock.patch.dict(os.environ, {standby.TEST_VAR: "1"}), mock.patch.object(standby, "READY", False):
            self.assertTrue(standby.ready(), "the test's HOME is a scratch one")

    def test_the_rig_in_a_scratch_home(self):
        """drill_rig(), what only the drill may do asks (DCBOT_DRILL_SENDER and the like): READY on opens none of it."""
        with mock.patch.dict(os.environ, {standby.TEST_VAR: "1"}):
            self.assertTrue(standby.drill_rig(), "the test's HOME is a scratch one")
            import pwd
            with mock.patch.dict(os.environ, {"HOME": pwd.getpwuid(os.getuid()).pw_dir}):
                self.assertFalse(standby.drill_rig(), "never in the real HOME")
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop(standby.TEST_VAR, None)
            self.assertFalse(standby.drill_rig(), "READY on: still not the rig")

    def test_the_setup_flow_and_the_skill(self):
        sys_path = os.path.join(ENGINE, "ops")
        if sys_path not in _sys.path:
            _sys.path.insert(0, sys_path)
        import setup as ops_setup
        with mock.patch.object(standby, "ready", lambda: False), mock.patch.object(ops_setup, "read_settings", lambda: {}):
            keys = [q["key"] for q in ops_setup.flow({"install": "new"}, [])]
        self.assertFalse([k for k in keys if k.startswith("standby")], "not asked while off")
        with mock.patch.object(standby, "ready", lambda: True), mock.patch.object(ops_setup, "read_settings", lambda: {}):
            keys = [q["key"] for q in ops_setup.flow({"install": "new"}, [])]
        self.assertIn("standby", keys, "asked once it is on (another machine on the list)")
        self.assertIn("healthchecks", keys, "the outside notice: asked of every new installation (d3b)")
        with open(os.path.join(ENGINE, "skills", "setup", "SKILL.md"), encoding="utf-8") as f:
            self.assertIn("「備援功能還在測試，這一版還不能設。」", f.read())


class Role(Rig):
    def test_added_and_committed(self):
        import subprocess
        subprocess.run(["git", "-C", self.inst, "init", "-q"], check=True)
        subprocess.run(["git", "-C", self.inst, "-c", "user.name=t", "-c", "user.email=t@x", "add", "-A"], check=True)
        subprocess.run(["git", "-C", self.inst, "-c", "user.name=t", "-c", "user.email=t@x", "commit", "-qm", "x"], check=True)
        with mock.patch.object(config, "instance_dir", lambda: self.inst):
            self.assertTrue(standby.add_role("host-b"))
            self.assertFalse(standby.add_role("host-b"), "once")
        with open(os.path.join(self.inst, "machines.json")) as f:
            self.assertEqual(json.load(f)["machines"]["host-b"]["roles"], ["dcbot-standby"])
        log = subprocess.run(["git", "-C", self.inst, "log", "--oneline"], capture_output=True, text=True).stdout
        self.assertIn("standby: host-b takes the role dcbot-standby", log)


class Questions(Rig):
    def test_only_with_another_machine(self):
        sys_path = os.path.join(ENGINE, "ops")
        if sys_path not in _sys.path:
            _sys.path.insert(0, sys_path)
        import plugins
        q = plugins.standby_questions(["host-b"], "host-a", "host-b")
        self.assertEqual(q[0]["values"], ["host-b", "no"])
        self.assertEqual(q[1]["values"], ["auto", "notify"])
        self.assertIn("多一台機器，就多一個可能被偷的地方", q[1]["question"])


if __name__ == "__main__":
    unittest.main()
