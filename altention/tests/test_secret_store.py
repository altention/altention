"""altention.secret_store (C-77 G3a): a plugin's secret stored 700/600 under ~/.config/altention/secrets, only a
declared name, never echoed or printed, #audit told without the value; the guard refuses that folder. In a
throwaway home; #audit is never really posted to."""
import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))))
from altention import testing as _testing  # noqa: E402
_testing.guard_home()  # tests never touch the real home (altention/testing.py)
import io
import os
import shutil
import stat
import tempfile
import unittest
from unittest import mock

from altention import plugin, plugin_steps, secret_store as ss, secrets

VALUE = "rig-value-0123456789"


class Rig(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="家 g3 ")
        self.addCleanup(shutil.rmtree, self.home, True)
        env = mock.patch.dict(os.environ, {"HOME": self.home})
        env.start()
        self.addCleanup(env.stop)
        self.d = {"name": "demo", "secrets": [{"name": "rig-token", "label": "Rig token"}],
                  "guide": [{"id": "key", "title": "拿金鑰", "check": 'test -s "$HOME/.config/altention/secrets/demo/rig-token"',
                             "secret": {"name": "rig-token", "label": "Rig token"}}]}
        rows = [("demo", self.home, self.d, "off", "不是它跑的機器")]
        self.audit = []
        for p in (mock.patch.object(ss.plugin, "on_this_machine", return_value=(rows, [])),
                  mock.patch.object(ss, "tell_audit", self.audit.append),
                  mock.patch.object(ss.config, "this_machine", return_value="lap")):
            p.start()
            self.addCleanup(p.stop)

    def run_(self, *argv, stdin="", tty=False, ask=None):
        out = io.StringIO()
        src = io.StringIO(stdin)
        src.isatty = lambda: tty
        with mock.patch("sys.stdout", out), mock.patch("sys.stdin", src), \
                mock.patch.object(ss.getpass, "getpass", ask or (lambda prompt: self.fail("asked"))):
            code = ss.main(list(argv))
        return code, out.getvalue()

    def file(self):
        return os.path.join(self.home, ".config", "altention", "secrets", "demo", "rig-token")


class Store(Rig):
    def test_stdin_stored_600_in_700_never_printed(self):
        code, out = self.run_("set", "demo", "rig-token", "--stdin", "--via", "discord", stdin=VALUE + "\n")
        self.assertEqual(code, 0, out)
        with open(self.file()) as f:
            self.assertEqual(f.read(), VALUE, "one trailing newline taken off")
        self.assertEqual(stat.S_IMODE(os.stat(self.file()).st_mode), 0o600)
        for folder in (os.path.dirname(self.file()), os.path.dirname(os.path.dirname(self.file()))):
            self.assertEqual(stat.S_IMODE(os.stat(folder).st_mode), 0o700, folder)
        self.assertNotIn(VALUE, out)
        self.assertIn("存好了：demo 的 rig-token（20 個字元", out)
        self.assertEqual(self.audit, ["🔑 lap 存了 demo 的 rig-token（從 Discord 的輸入框）"])
        self.assertFalse(os.path.exists(self.file() + ".new"))

    def test_terminal_asks_without_echo_and_refuses_without_one(self):
        asked = []
        code, out = self.run_("set", "demo", "rig-token", tty=True, ask=lambda prompt: asked.append(prompt) or VALUE)
        self.assertEqual(code, 0, out)
        self.assertEqual(asked, ["demo 的 rig-token（貼上後按 Enter，不會顯示）："])
        self.assertIn("從終端機", self.audit[0])
        code, out = self.run_("set", "demo", "rig-token", tty=False)
        self.assertEqual(code, 1)
        self.assertIn("要在你自己的終端機跑", out)

    def test_only_a_declared_name_a_value_and_a_plugin_known(self):
        for argv, stdin, words in ((("set", "demo", "other", "--stdin"), VALUE, "沒有宣告叫 other 的金鑰"),
                                   (("set", "nope", "rig-token", "--stdin"), VALUE, "找不到 nope"),
                                   (("set", "demo", "rig-token", "--stdin"), " \n", "是空的"),
                                   (("set", "demo", "rig-token", "--stdin"), "x" * (ss.MAX + 1), "太長了"),
                                   (("set", "demo", "../x", "--stdin"), VALUE, "沒有宣告")):
            code, out = self.run_(*argv, stdin=stdin)
            self.assertEqual(code, 1, (argv, out))
            self.assertIn(words, out, argv)
        self.assertFalse(os.path.exists(os.path.dirname(self.file())))
        self.assertEqual(self.audit, [])
        with self.assertRaises(ss.Refused):
            ss.path("demo", "../../x")
        self.assertEqual(self.run_("set", "demo", "rig-token", "--via", "mail", "--stdin", stdin=VALUE)[0], 2)

    def test_the_other_machines_using_it_are_named_after_storing(self):
        with mock.patch.object(ss.plugin, "users_elsewhere", return_value=["far", "mid"]) as elsewhere:
            code, out = self.run_("set", "demo", "rig-token", "--stdin", stdin=VALUE)
        self.assertEqual(code, 0, out)
        self.assertEqual(out.splitlines()[1], "demo 在 far、mid 也有用：要把這把金鑰也放過去，到 #setup 跟 Claude 說「把 demo 的 rig-token 也放到 <機器>」")
        self.assertEqual(elsewhere.call_args[0][1], "demo")
        with mock.patch.object(ss.plugin, "users_elsewhere", return_value=[]):
            self.assertEqual(len(self.run_("set", "demo", "rig-token", "--stdin", stdin=VALUE)[1].splitlines()), 1)
        with mock.patch.object(ss.config, "settings", side_effect=ss.config.ConfigError("broken")):
            code, out = self.run_("set", "demo", "rig-token", "--stdin", stdin=VALUE)
        self.assertEqual((code, len(out.splitlines())), (0, 1), "stored all the same, nothing said of the others")

    def test_list_names_lengths_only(self):
        self.assertIn("demo/rig-token：這台還沒有", self.run_("list")[1])
        self.run_("set", "demo", "rig-token", "--stdin", stdin=VALUE)
        code, out = self.run_("list", "demo")
        self.assertEqual(out, "demo/rig-token：這台有（20 個字元）\n")
        self.assertEqual(ss.length("demo", "rig-token"), 20)

    def test_a_stored_one_is_said_in_the_plan(self):
        p = plugin_steps.plan("demo", self.home, self.d)
        self.assertFalse(p["guide"][0]["stored"])
        self.assertIn("（金鑰：這台還沒存；在自己的終端機跑 altention secret set demo rig-token）", "\n".join(plugin_steps.lines(p)))
        ss.write("demo", "rig-token", VALUE)
        p = plugin_steps.plan("demo", self.home, self.d)
        self.assertEqual((p["guide"][0]["stored"], p["guide"][0]["state"]), (True, "ok"))


class Guard(unittest.TestCase):
    def test_the_folder_is_refused_to_read_write_and_name(self):
        home = "/srv/rig"
        with mock.patch.object(secrets, "extra", return_value=([], None)):
            listed = secrets.patterns(home=home)[0]
            self.assertIn(home + "/.config/altention/secrets/**", listed)
            for p in (home + "/.config/altention/secrets/demo/rig-token", home + "/.config/altention/secrets/x"):
                self.assertTrue(secrets.is_secret(p, listed), p)
            self.assertFalse(secrets.is_secret(home + "/.config/altention/host", listed))
            self.assertTrue(secrets.mentions("cat ~/.config/altention/secrets/demo/rig-token", listed, home=home))
            self.assertFalse(secrets.mentions("altention secret set demo rig-token", listed, home=home))
            self.assertIn("Read(~/.config/altention/secrets/**)", secrets.deny_rules(home=home))


class Fields(unittest.TestCase):
    def test_secrets_name_and_retired_path(self):
        base = {"format": 1, "name": "demo", "title": "D", "description": "d"}
        plugin.check(dict(base, secrets=[{"name": "rig-token", "label": "L"}]))
        with self.assertRaises(plugin.PluginError):
            plugin.check(dict(base, secrets=[{"name": "Rig/Token"}]))
        d = plugin.check(dict(base, secrets=[{"name": "rig-token", "path": "~/x"}]))
        self.assertEqual([w for w, _ in plugin.retired(d)], ["secrets[].path"])
        core = dict(base, name="core", secrets=[{"name": "bot", "path": "~/.config/dcbot/env"}])
        self.assertEqual(plugin.retired(core), [], "the core's secrets lie elsewhere")


if __name__ == "__main__":
    unittest.main()
