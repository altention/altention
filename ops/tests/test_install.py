"""ops/install.py (the installer behind install.sh, stage 3a.9): the quick parts, in a throwaway home. The whole run
in fake homes is tests/install/acceptance.py (about a minute, not run by deploy.sh)."""
import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))))
from altention import testing as _testing  # noqa: E402
_testing.guard_home()  # tests never touch the real home (altention/testing.py)
import builtins
import io
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import install  # noqa: E402

ENGINE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
LINE = re.compile(r"^(ok|changed|skip|fail|todo|unknown) [a-z0-9][a-z0-9.-]* .+$")


class Base(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="家 install ")
        self.env = mock.patch.dict(os.environ, {"HOME": self.home, "ALT_INSTANCE": "", "XDG_STATE_HOME": ""})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        shutil.rmtree(self.home, ignore_errors=True)

    def run_(self):
        out = io.StringIO()
        with redirect_stdout(out):
            r = install.Run()
        return r, out


class Lines(Base):
    def test_codes(self):
        r, _ = self.run_()
        with redirect_stdout(io.StringIO()):
            r.say("ok", "a.b", "x")
            self.assertEqual(r.code(), 0)
            r.say("todo", "a.c", "do this")
            self.assertEqual(r.code(), 2)
            r.say("unknown", "a.d", "cannot tell")
            self.assertEqual(r.code(), 1, "unknown counts as 1 (manager 2026-10-06)")

    def test_the_log_is_600_and_never_the_environment(self):
        r, _ = self.run_()
        with mock.patch.dict(os.environ, {"SECRET_LOOKING": "do-not-log-me"}):
            r.script("echo", ["/bin/echo", "hello"])
        with open(r.log_path) as f:
            text = f.read()
        self.assertIn("hello", text)
        self.assertNotIn("do-not-log-me", text)
        self.assertEqual(stat.S_IMODE(os.stat(r.log_path).st_mode), 0o600)

    def test_an_unknown_argument_does_nothing(self):
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(install.main(["--wirte"]), 1)
        self.assertTrue(out.getvalue().startswith("fail core.args"))
        self.assertFalse(os.path.exists(os.path.join(self.home, ".local")), "not even the log folder")

    def test_the_light_part_without_an_instance(self):
        """install.sh --links on a machine with no instance: no instance made, every line in the format, exit 0."""
        p = subprocess.run([os.path.join(ENGINE, "install.sh"), "--links"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           universal_newlines=True, env=dict(os.environ, HOME=self.home), stdin=subprocess.DEVNULL)
        lines = [x for x in p.stdout.splitlines() if x.strip()]
        self.assertEqual(p.returncode, 0, p.stdout)
        self.assertTrue(all(LINE.match(x) for x in lines), p.stdout)
        self.assertIn("skip core.instance", p.stdout)
        self.assertEqual(lines[-1].split()[:2], ["ok", "core.done"])
        self.assertFalse(os.path.exists(os.path.join(self.home, "altention")))


class Steps(Base):
    """A step that throws is its own fail, in words; what needed it is a skip with the reason; the last line says it
    did not finish (#code-review-2 2026-10-06)."""

    def test_a_write_that_fails_is_said_in_words_and_what_needs_it_is_skipped(self):
        r, out = self.run_()
        st = install.Steps(r)

        def unwritable():
            raise PermissionError(13, "Permission denied", os.path.join(self.home, ".ssh", "config.altention-tmp"))
        with redirect_stdout(out):
            st.run("core.ssh", unwritable)
            st.run("core.apply", lambda: r.say("ok", "core.apply", "x"), needs=["core.ssh"])
            st.run("core.git-user", lambda: r.say("ok", "core.git-user", "x"))
            install.done(r, st, "安裝")
        lines = out.getvalue().splitlines()
        self.assertTrue(lines[0].startswith("fail core.ssh 寫不進 %s" % os.path.join(self.home, ".ssh")), lines[0])
        self.assertIn("檢查它的權限", lines[0])
        self.assertNotIn("PermissionError", lines[0])
        self.assertTrue(lines[1].startswith("skip core.apply 沒做：前面的 core.ssh 沒成功"), lines[1])
        self.assertTrue(lines[2].startswith("ok core.git-user"), "not needing it: still done")
        self.assertTrue(lines[-1].startswith("fail core.done 安裝沒跑完：core.ssh"), lines[-1])
        self.assertEqual(r.code(), 1)

    def test_what_is_left_to_the_user_is_named_on_the_last_line(self):
        """A todo: exit 2, and the last line still starts "ok core.done " (ops/apply.py waits for it) and names it."""
        r, out = self.run_()
        st = install.Steps(r)
        with redirect_stdout(out):
            st.run("dcbot.token", lambda: r.say("todo", "dcbot.token", "x"))
            st.run("core.git-user", lambda: r.say("ok", "core.git-user", "x"))
            install.done(r, st, "安裝")
        last = out.getvalue().splitlines()[-1]
        self.assertTrue(last.startswith("ok core.done 安裝跑完了，但還有要你做的：dcbot.token"), last)
        self.assertEqual(r.code(), 2)
        r, out = self.run_()
        with redirect_stdout(out):
            install.done(r, install.Steps(r), "安裝")
        self.assertEqual(out.getvalue().strip(), "ok core.done 安裝跑完了")

    def test_no_crontab_command_is_a_todo_with_how_to_install_it(self):
        r, out = self.run_()
        with mock.patch.object(install.shutil, "which", return_value=None), \
                mock.patch.object(r, "script", side_effect=AssertionError("plugins.py apply must not run")), redirect_stdout(out):
            install.write_schedules(r, [], [])
        self.assertRegex(out.getvalue(), r"^todo core.apply 沒有 crontab 指令.*apt install cron.*cronie")
        import common
        self.assertIn(common.INSTALL_CRON, out.getvalue(), "the same words as doctor's fix")
        self.assertEqual(r.code(), 2)

    def test_a_crontab_with_no_cron_daemon_running_is_a_todo(self):
        """WSL and containers: the lines go in, nothing runs them; said at install, the same words (manager 2026-10-15)."""
        import common
        for running, todo in ((False, True), (True, False), (None, False)):
            r, out = self.run_()
            with mock.patch.object(install.shutil, "which", return_value="/usr/bin/crontab"), \
                    mock.patch.object(r, "script", return_value=(0, "%s plugins-apply" % install.MARK)), \
                    mock.patch.object(common, "cron_running", return_value=running), redirect_stdout(out):
                install.write_schedules(r, [], [])
            self.assertEqual("todo core.cron-service cron 服務沒在跑" in out.getvalue(), todo, (running, out.getvalue()))
            self.assertEqual(r.code(), 2 if todo else 0, running)
            if todo:
                self.assertIn(common.START_CRON, out.getvalue())
                self.assertNotIn("先裝 cron", out.getvalue(), "cron is there: only its service is to be started")

    def test_a_plugin_install_that_fails_says_its_last_lines_and_where_the_rest_is(self):
        """support-4 dry run 2026-10-15: only "curl: (23)" was said; the cause (xz: Cannot exec) was in the log."""
        r, out = self.run_()
        said = "  something before\n\nxz: Cannot exec: No such file or directory\ntar: Child returned status 2\ncurl: (23) Failure writing output\n"
        with mock.patch.object(r, "script", return_value=(2, said)), redirect_stdout(out):
            self.assertFalse(install.install_plugins(r, [("usage", "usage"), ("planner", "planner")]))
        line = out.getvalue().strip()
        self.assertEqual(len(line.splitlines()), 1, "one line, the next plugin not run")
        self.assertTrue(line.startswith("fail plugin.usage usage/install.sh 失敗（結束碼 2）："), line)
        self.assertIn("xz: Cannot exec: No such file or directory / tar: Child returned status 2 / curl: (23)", line)
        self.assertIn("完整輸出看 %s" % r.log_path, line)
        self.assertRegex(line, LINE)
        r, out = self.run_()
        with mock.patch.object(r, "script", return_value=(1, "")), redirect_stdout(out):
            install.install_plugins(r, [("usage", "usage")])
        self.assertIn("失敗（結束碼 1）：沒有輸出；完整輸出看", out.getvalue())

    def test_cron_running_reads_pgrep_on_linux_only(self):
        import common
        calls = []

        def pgrep(rcs):
            def run(argv, **kw):
                calls.append(argv)
                return mock.Mock(returncode=rcs.pop(0))
            return run
        with mock.patch.object(common.sys, "platform", "linux"):
            self.assertIs(common.cron_running(pgrep([1, 0])), True)
            self.assertEqual(calls, [["pgrep", "-x", "cron"], ["pgrep", "-x", "crond"]])
            self.assertIs(common.cron_running(pgrep([1, 1])), False)
            self.assertIsNone(common.cron_running(pgrep([2])), "pgrep failing: not known")
            self.assertIsNone(common.cron_running(mock.Mock(side_effect=OSError("no pgrep"))))
        with mock.patch.object(common.sys, "platform", "darwin"):
            self.assertIsNone(common.cron_running(mock.Mock(side_effect=AssertionError("not asked on macOS"))))

    def test_a_bug_of_ours_is_one_line_the_traceback_in_the_log(self):
        r, out = self.run_()
        st = install.Steps(r)
        with redirect_stdout(out):
            st.run("core.links", lambda: {}["nope"])
        self.assertEqual(out.getvalue().strip(), "fail core.links 程式出錯（KeyError），詳見 log")
        with open(r.log_path) as f:
            self.assertIn("Traceback", f.read())

    def test_claude_folder_being_a_file(self):
        with open(os.path.join(self.home, ".claude"), "w") as f:
            f.write("not a folder")
        r, out = self.run_()
        st = install.Steps(r)
        with redirect_stdout(out):
            st.run("core.skills", install.skills, r)
        self.assertTrue(out.getvalue().startswith("fail core.skills 寫不進"), out.getvalue())


class SkillsOff(Base):
    """A plugin turned off here (disabled_on) has no skill here: council's "lose" says 「說「召開議會」也沒有這個
    skill」, so its link goes (#architecture-review, choice c). Only a link to this engine's own folder is taken away;
    a folder or a link elsewhere under that name is left and said. report belongs to progress."""

    def setUp(self):
        super().setUp()
        self.inst = os.path.join(self.home, "實例")
        os.makedirs(self.inst)  # no machines.json: this machine alone
        os.makedirs(os.path.join(self.home, ".config", "altention"))
        with open(os.path.join(self.home, ".config", "altention", "host"), "w") as f:
            f.write("box\n")
        self.env2 = mock.patch.dict(os.environ, {"ALT_INSTANCE": self.inst})
        self.env2.start()
        self.skills = os.path.join(self.home, ".claude", "skills")

    def tearDown(self):
        self.env2.stop()
        super().tearDown()

    def off(self, *names):
        with open(os.path.join(self.inst, "plugins.json"), "w") as f:
            json.dump({"format": 1, "plugins": dict((n, {"disabled_on": ["box"]}) for n in names)}, f)

    def link(self, name):
        p = os.path.join(self.skills, name)
        return os.readlink(p) if os.path.islink(p) else ("folder" if os.path.isdir(p) else None)

    def install(self):
        r, out = self.run_()
        with redirect_stdout(out):
            install.skills(r)
        return out.getvalue()

    def test_turned_off_its_link_goes_turned_on_it_comes_back(self):
        self.off()
        self.install()
        self.assertEqual((self.link("council"), self.link("report")),
                         (os.path.join(ENGINE, "skills", "council"), os.path.join(ENGINE, "skills", "report")))
        self.off("council")
        out = self.install()
        self.assertEqual((self.link("council"), self.link("report")), (None, os.path.join(ENGINE, "skills", "report")))
        self.assertIn("changed core.skills skill 連結：report；關掉的：council（拿掉了 council）", out)
        self.assertIn("ok core.skills skill 連結：report；關掉的：council\n", self.install(), "said again, nothing to take")
        self.off("progress")
        self.install()
        self.assertEqual((self.link("council"), self.link("report")), (os.path.join(ENGINE, "skills", "council"), None),
                         "report is progress's")
        self.off()
        self.install()
        self.assertEqual(self.link("report"), os.path.join(ENGINE, "skills", "report"))

    def test_what_is_not_this_engines_link_is_left_and_said(self):
        self.off("council", "progress")
        os.makedirs(os.path.join(self.skills, "council"))
        os.symlink("/elsewhere/report", os.path.join(self.skills, "report"))
        out = self.install()
        self.assertEqual((self.link("council"), self.link("report")), ("folder", "/elsewhere/report"))
        self.assertEqual(out.count("skip core.skills-off"), 2, out)
        self.assertIn("不是連到這份引擎的連結，沒有動", out)
        self.assertTrue(all(LINE.match(x) for x in out.splitlines() if x.strip()), out)

    def test_settings_that_cannot_be_read_turn_nothing_off(self):
        with open(os.path.join(self.inst, "plugins.json"), "w") as f:
            f.write("{ not json")
        self.install()
        self.assertEqual(self.link("council"), os.path.join(ENGINE, "skills", "council"))

    def test_who_owns_which_skill(self):
        from altention import plugin
        rows, _ = plugin.on_this_machine()
        self.assertEqual(plugin.skill_owners(rows), {"council": "council", "report": "progress"})


class Entry(Base):
    def test_a_file_of_the_users_at_the_commands_place_is_a_todo(self):
        for name in ("altention", "alt"):
            with self.subTest(name=name):
                shutil.rmtree(os.path.join(self.home, ".local"), ignore_errors=True)
                path = os.path.join(self.home, ".local", "bin", name)
                os.makedirs(os.path.dirname(path))
                with open(path, "w") as f:
                    f.write("#!/bin/sh\necho mine\n")
                r, out = self.run_()
                with redirect_stdout(out):
                    install.entry(r)
                self.assertIn("todo core.entry %s 是別人的檔" % path, out.getvalue())
                self.assertIn("mv %s %s.mine" % (path, path), out.getvalue())
                with open(path) as f:
                    self.assertEqual(f.read(), "#!/bin/sh\necho mine\n")


class PluginsHere(Base):
    def rows(self):
        return [("core", "/e/altention", {}, "on", ""),
                ("chatlog", os.path.join(install.ENGINE, "chatlog"), None, "unknown", "plugin.json 讀不出來"),
                ("progress", os.path.join(install.ENGINE, "progress"), {"machines": "one", "requires": [{"plugin": "chatlog"}]}, "on", ""),
                ("usage", os.path.join(install.ENGINE, "usage"), {"machines": "one"}, "on", "")]

    def test_only_the_unknown_one_and_what_needs_it_are_left_out(self):
        r, out = self.run_()
        with mock.patch.object(install.config, "settings", return_value={}), \
                mock.patch.object(install.config, "inventory", return_value={}), \
                mock.patch.object(install.config, "bot_machine", return_value=(None, None, False)), \
                mock.patch.object(install.plugin, "on_this_machine", return_value=(self.rows(), [])), redirect_stdout(out):
            found, bot_here = install.plugins_here(r, [])
        self.assertEqual([n for n, _ in found], ["usage"])
        self.assertIn("unknown plugin.chatlog", out.getvalue())
        self.assertIn("unknown plugin.progress progress 要用的 chatlog 不知道", out.getvalue())
        self.assertEqual(sorted(install.plugins_here.held), ["chatlog", "progress"], "plugins.py apply leaves them alone too")

    def test_needs_are_followed_however_far_and_in_any_order(self):
        rows = [("a", os.path.join(install.ENGINE, "a"), {"requires": [{"plugin": "b"}]}, "on", ""),
                ("b", os.path.join(install.ENGINE, "b"), {"requires": [{"plugin": "c"}]}, "on", ""),
                ("c", os.path.join(install.ENGINE, "c"), None, "unknown", "plugin.json 讀不出來"),
                ("d", os.path.join(install.ENGINE, "usage"), {}, "on", "")]
        r, out = self.run_()
        with mock.patch.object(install.config, "settings", return_value={}), \
                mock.patch.object(install.config, "inventory", return_value={}), \
                mock.patch.object(install.config, "bot_machine", return_value=(None, None, False)), \
                mock.patch.object(install.plugin, "on_this_machine", return_value=(rows, [])), redirect_stdout(out):
            found, _ = install.plugins_here(r, [])
        self.assertEqual(sorted(install.plugins_here.held), ["a", "b", "c"], "a needs b needs c: all three wait")
        self.assertEqual([n for n, _ in found], ["d"])

    def test_settings_that_cannot_be_read_leave_everything(self):
        r, out = self.run_()
        with mock.patch.object(install.config, "settings", side_effect=install.config.ConfigError("壞了")), \
                redirect_stdout(out):
            self.assertIsNone(install.plugins_here(r, []))
        self.assertIn("unknown core.plugins", out.getvalue())


class Token(Base):
    def test_never_opened_only_its_size(self):
        path = os.path.join(self.home, ".config", "dcbot", "env")
        os.makedirs(os.path.dirname(path))
        with open(path, "w") as f:
            f.write("DISCORD_TOKEN=FAKE-TOKEN-NOT-REAL\n")
        real_open = builtins.open

        def guarded(p, *a, **k):
            if str(p).endswith(os.path.join("dcbot", "env")):
                raise AssertionError("the token file was opened")
            return real_open(p, *a, **k)
        r, out = self.run_()
        with mock.patch.object(builtins, "open", guarded), redirect_stdout(out):
            install.token(r, True)
        self.assertIn("ok dcbot.token", out.getvalue())
        os.remove(path)
        out = io.StringIO()
        with redirect_stdout(out):
            install.token(r, True)
        self.assertIn("todo dcbot.token", out.getvalue())
        self.assertIn(path, out.getvalue(), "the todo names the file")


class AskToken(Base):
    """install.sh --token: typed on the keyboard, written 600, never shown; nothing written unless all of it is right."""
    TOK = "FAKEFAKEFAKE.not-a.real-token"
    OWNER, GUILD = "100000000000000001", "100000000000000002"

    def setUp(self):
        super().setUp()
        self.path = os.path.join(self.home, ".config", "dcbot", "env")

    def ask(self, answers, secret=None, interactive=True):
        answers = list(answers)
        out = io.StringIO()
        with redirect_stdout(out):
            rc = install.ask_token(secret=lambda prompt: secret if secret is not None else self.TOK,
                                   line=lambda prompt: answers.pop(0), interactive=interactive)
        return rc, out.getvalue()

    def lines(self):
        with open(self.path) as f:
            return f.read().splitlines()

    def test_written_600_in_a_700_folder_and_never_shown(self):
        rc, out = self.ask([self.OWNER, self.GUILD])
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.lines(), ["DISCORD_TOKEN=" + self.TOK, "OWNER_ID=" + self.OWNER, "GUILD_ID=" + self.GUILD])
        self.assertEqual(stat.S_IMODE(os.stat(self.path).st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(os.stat(os.path.dirname(self.path)).st_mode), 0o700)
        self.assertNotIn(self.TOK, out)
        self.assertIn("changed dcbot.token", out)
        self.assertEqual(os.listdir(os.path.dirname(self.path)), ["env"], "no temporary file left")

    def test_from_anything_but_a_terminal_it_refuses(self):
        rc, out = self.ask([], interactive=False)
        self.assertEqual(rc, 1)
        self.assertIn("fail dcbot.token", out)
        self.assertFalse(os.path.exists(os.path.dirname(self.path)))
        with mock.patch.object(sys.stdin, "isatty", return_value=False), redirect_stdout(io.StringIO()) as o:
            self.assertEqual(install.main(["--token"]), 1)
        self.assertIn("fail dcbot.token", o.getvalue())

    def test_anything_wrong_writes_nothing(self):
        for name, answers, secret in (("empty token", [self.OWNER, self.GUILD], ""),
                                      ("token with a space", [self.OWNER, self.GUILD], "a.b c.d"),
                                      ("not three parts", [self.OWNER, self.GUILD], "abcdef"),
                                      ("owner not digits", ["alice", self.GUILD], None),
                                      ("guild too short", [self.OWNER, "12345"], None)):
            rc, out = self.ask(answers, secret=secret)
            self.assertEqual(rc, 1, name)
            self.assertFalse(os.path.exists(self.path), name)
            self.assertTrue(all(LINE.match(x) for x in out.splitlines() if x.startswith(("ok ", "fail ", "changed "))), name)

    def test_a_wrong_token_says_what_was_read_but_not_the_token(self):
        rc, out = self.ask([], secret="")
        self.assertEqual(rc, 1)
        self.assertIn("沒有讀到任何字", out)
        for secret, said in (("abcdef", "讀到 6 個字、0 個點（"), ("a.b c.d", "讀到 7 個字、2 個點、中間有空白"),
                             ("SECRETPART.x.y.z", "讀到 16 個字、3 個點（")):
            rc, out = self.ask([], secret=secret)
            self.assertEqual(rc, 1, secret)
            self.assertIn(said, out)
            self.assertNotIn(secret, out)
            self.assertNotIn("SECRETPART", out)

    def test_stopped_halfway_writes_nothing(self):
        def stop(prompt):
            raise EOFError
        with redirect_stdout(io.StringIO()) as out:
            rc = install.ask_token(secret=lambda p: self.TOK, line=stop, interactive=True)
        self.assertEqual(rc, 1)
        self.assertIn("什麼都沒寫", out.getvalue())
        self.assertFalse(os.path.exists(self.path))

    def test_an_existing_file_is_kept_unless_asked_and_its_other_lines_stay(self):
        os.makedirs(os.path.dirname(self.path))
        with open(self.path, "w") as f:
            f.write("# 我的設定\nDISCORD_TOKEN=OLD.OLD.OLD\nMODEL=opus\nOWNER_ID=100000000000000009\n")
        os.chmod(self.path, 0o600)
        before = self.lines()
        rc, out = self.ask(["", self.OWNER, self.GUILD])
        self.assertEqual((rc, self.lines()), (0, before), "Enter keeps it")
        self.assertIn("ok dcbot.token", out)
        rc, out = self.ask(["y", self.OWNER, self.GUILD])
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.lines(), ["DISCORD_TOKEN=" + self.TOK, "OWNER_ID=" + self.OWNER, "GUILD_ID=" + self.GUILD,
                                        "# 我的設定", "MODEL=opus"])
        self.assertEqual(stat.S_IMODE(os.stat(self.path).st_mode), 0o600)

    def test_every_spelling_of_a_replaced_key_goes(self):
        """export KEY=old, KEY = old, a second line of the same key: no old token stays in the file."""
        os.makedirs(os.path.dirname(self.path))
        with open(self.path, "w") as f:
            f.write("export DISCORD_TOKEN=OLD.OLD.OLD\nDISCORD_TOKEN = OLD2.OLD.OLD\nexport\tGUILD_ID=100000000000000008\n"
                    "exported_TOKEN=keep\nexport MODEL=opus\n")
        rc, out = self.ask(["y", self.OWNER, self.GUILD])
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.lines(), ["DISCORD_TOKEN=" + self.TOK, "OWNER_ID=" + self.OWNER, "GUILD_ID=" + self.GUILD,
                                        "exported_TOKEN=keep", "export MODEL=opus"])
        with open(self.path) as f:
            self.assertNotIn("OLD", f.read())

    def test_no_bot_anywhere_is_said_with_the_command(self):
        r, out = self.run_()
        with mock.patch.object(install.config, "bot_machine", return_value=(None, None, False)), redirect_stdout(out):
            install.token(r, False)
        self.assertRegex(out.getvalue(), r"todo dcbot.token .*install.sh --token")
        self.assertEqual(r.code(), 2, "no bot anywhere yet: the install is the user's to finish (support-4 dry run #1)")
        r, out = self.run_()
        with mock.patch.object(install.config, "bot_machine", return_value=("host-b", {}, False)), redirect_stdout(out):
            install.token(r, False)
        self.assertNotIn("dcbot.token", out.getvalue(), "the bot is on another machine: nothing to say here")


class Ssh(Base):
    def setUp(self):
        super().setUp()
        self.inst = os.path.join(self.home, "inst")
        os.makedirs(self.inst)
        with open(os.path.join(self.inst, "ssh_config"), "w") as f:
            f.write("Host other\n")
        self.cfg = os.path.join(self.home, ".ssh", "config")

    def ssh(self, machines):
        r, out = self.run_()
        with mock.patch.object(install.config, "inventory", return_value={"machines": machines}), redirect_stdout(out):
            install.ssh(r, self.inst)
        return out.getvalue(), r

    def test_one_machine_never_touches_ssh(self):
        os.makedirs(os.path.dirname(self.cfg))
        with open(self.cfg, "w") as f:
            f.write("Host mine\n")
        os.utime(self.cfg, (1000000000, 1000000000))
        text, _ = self.ssh({"a": {}})
        self.assertIn("skip core.ssh", text)
        self.assertEqual(os.stat(self.cfg).st_mtime, 1000000000)

    def test_made_600_with_the_include_when_there_is_none(self):
        text, _ = self.ssh({"a": {}, "b": {}})
        self.assertIn("changed core.ssh", text)
        self.assertEqual(stat.S_IMODE(os.stat(self.cfg).st_mode), 0o600)
        with open(self.cfg) as f:
            self.assertEqual(f.read(), "Include %s\n" % os.path.join(self.inst, "ssh_config"))
        text, _ = self.ssh({"a": {}, "b": {}})
        self.assertIn("ok core.ssh", text, "a second run changes nothing")

    def test_backed_up_first_mode_kept_other_lines_byte_for_byte(self):
        os.makedirs(os.path.dirname(self.cfg))
        mine = "Host mine\n  HostName 192.0.2.1\nInclude /old/engine/ops/ssh_config\n# note\n"
        with open(self.cfg, "w") as f:
            f.write(mine)
        os.chmod(self.cfg, 0o640)
        text, r = self.ssh({"a": {}, "b": {}})
        self.assertIn("changed core.ssh", text)
        with open(self.cfg) as f:
            after = f.read()
        self.assertEqual(after, "Include %s\n\n" % os.path.join(self.inst, "ssh_config") + mine, "an old engine's Include is no longer swapped")
        self.assertEqual(stat.S_IMODE(os.stat(self.cfg).st_mode), 0o640)
        backups = [os.path.join(dp, f) for dp, _, fs in os.walk(os.path.join(r.folder, "backup")) for f in fs]
        self.assertEqual(len(backups), 1)
        with open(backups[0]) as f:
            self.assertEqual(f.read(), mine)

    def test_a_write_that_reads_back_wrong_is_put_back(self):
        os.makedirs(os.path.dirname(self.cfg))
        mine = "Host mine\n"
        with open(self.cfg, "w") as f:
            f.write(mine)
        real, done = os.replace, []

        def spoil(src, dst):  # the first write only: the putting back is real
            if dst == self.cfg and src.endswith(".altention-tmp") and not done:
                done.append(1)
                with open(src, "a") as f:
                    f.write("Host extra\n")
            return real(src, dst)
        with mock.patch.object(install.os, "replace", spoil):
            text, _ = self.ssh({"a": {}, "b": {}})
        self.assertIn("fail core.ssh", text)
        with open(self.cfg) as f:
            self.assertEqual(f.read(), mine)


class Instance(Base):
    def test_a_named_instance_not_there_is_said_as_an_instance(self):
        out = io.StringIO()
        with mock.patch.dict(os.environ, {"ALT_INSTANCE": os.path.join(self.home, "nowhere")}), redirect_stdout(out):
            self.assertEqual(install.main([]), 1)
        self.assertTrue(out.getvalue().startswith("fail core.instance "), out.getvalue())
        self.assertIn("改正 ALT_INSTANCE", out.getvalue())
        self.assertNotIn("core.engine", out.getvalue())

    def test_a_named_folder_that_is_not_there_fails_and_makes_nothing(self):
        r, out = self.run_()
        with mock.patch.dict(os.environ, {"ALT_INSTANCE": os.path.join(self.home, "nowhere")}), redirect_stdout(out):
            self.assertIsNone(install.find_instance(r, False))
        self.assertIn("fail core.instance", out.getvalue())
        self.assertFalse(os.path.exists(os.path.join(self.home, "altention")))

    def test_something_else_at_the_default_place_is_a_todo_left_alone(self):
        place = os.path.join(self.home, "altention", "instance")
        os.makedirs(place)
        with open(os.path.join(place, "mine.txt"), "w") as f:
            f.write("x")
        r, out = self.run_()
        with redirect_stdout(out):
            self.assertIsNone(install.find_instance(r, False))
        self.assertIn("todo core.instance", out.getvalue())
        self.assertIn("mv ", out.getvalue())
        self.assertEqual(os.listdir(place), ["mine.txt"])

    def test_a_new_instance_is_linked_into_an_unpacked_engine_by_the_install(self):
        """support-4 dry run #1: knowledge/ and memory/ point at the instance after install.sh, not after the first
        sync (which never links an engine with no .git when it fails on it)."""
        import instance as ops_instance
        eng = os.path.join(self.home, "altention", "engine")
        os.makedirs(os.path.join(eng, "altention"))
        with mock.patch.object(install.config, "ENGINE", eng), mock.patch.object(ops_instance, "ENGINE", eng), \
                redirect_stdout(io.StringIO()):
            self.assertEqual(ops_instance.init(), 0)
        inst = os.path.join(self.home, "altention", "instance")
        self.assertEqual(install.config.read_json(os.path.join(inst, "plugins.json"))["sync"]["owns"], ["knowledge"])
        self.assertTrue(os.path.isfile(os.path.join(inst, "knowledge", "README.md")), "committed, so other machines get it")
        r, out = self.run_()
        with mock.patch.object(install.config, "ENGINE", eng), redirect_stdout(out):
            install.links(r, inst)
        for name in ("knowledge", "memory"):
            self.assertEqual(os.readlink(os.path.join(eng, name)), os.path.join(inst, name))
        self.assertRegex(out.getvalue(), r"(?m)^changed core.engine-links .*knowledge、memory")
        r, out = self.run_()
        with mock.patch.object(install.config, "ENGINE", eng), redirect_stdout(out):
            install.links(r, inst)
        self.assertRegex(out.getvalue(), r"(?m)^ok core.engine-links", "run again: nothing to change")


class InstallNode(Base):
    """usage/install-node.sh: the .tar.gz of this system's node (no xz needed: minimal Ubuntu has none, support-4
    2026-10-15), macOS's own build on a Mac. uname, curl and the tarball are stand-ins; tar and gzip are the real ones."""

    def node(self, system, machine, curl_ok=True):
        bin_ = os.path.join(self.home, "stub bin")
        os.makedirs(bin_, exist_ok=True)
        tgz = os.path.join(self.home, "node.tar.gz")
        for name, body in (("uname", 'case "$1" in -s) echo %s ;; -m) echo %s ;; esac' % (system, machine)),
                           ("xz", "echo 'xz: not here' >&2; exit 127"),  # as on minimal Ubuntu: never to be needed
                           ("curl", 'echo "$@" >> "$HOME/curl.args"; [ -n "$CURL_OK" ] || exit 22\n'
                                    'while [ "$1" != -o ]; do shift; done; cp "$TGZ" "$2"')):
            with open(os.path.join(bin_, name), "w") as f:
                f.write("#!/bin/sh\n%s\n" % body)
            os.chmod(os.path.join(bin_, name), 0o755)
        top = "node-v22.12.0-%s-%s" % ({"Linux": "linux", "Darwin": "darwin"}.get(system, "x"), {"x86_64": "x64"}.get(machine, machine))
        src = os.path.join(self.home, "src", top, "bin")
        os.makedirs(src, exist_ok=True)
        for name, said in (("node", "v22.12.0"), ("ccusage", "20.0.0")):
            with open(os.path.join(src, name), "w") as f:
                f.write("#!/bin/sh\necho %s\n" % said)
            os.chmod(os.path.join(src, name), 0o755)
        subprocess.run(["tar", "czf", tgz, "-C", os.path.join(self.home, "src"), top], check=True)
        env = dict(os.environ, HOME=self.home, PATH=bin_ + ":/usr/bin:/bin", TGZ=tgz, CURL_OK="1" if curl_ok else "")
        p = subprocess.run([os.path.join(ENGINE, "usage", "install-node.sh")], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           universal_newlines=True, env=env, timeout=60)
        try:
            with open(os.path.join(self.home, "curl.args")) as f:
                args = f.read()
        except OSError:
            args = ""
        return p.returncode, p.stdout, args, os.path.join(self.home, ".local", "opt", top)

    def test_the_tar_gz_of_this_system_and_machine(self):
        for system, machine, url in (("Linux", "x86_64", "node-v22.12.0-linux-x64.tar.gz"),
                                     ("Darwin", "arm64", "node-v22.12.0-darwin-arm64.tar.gz")):
            rc, out, args, place = self.node(system, machine)
            self.assertEqual(rc, 0, out)
            self.assertIn("https://nodejs.org/dist/v22.12.0/" + url, args)
            self.assertIn("node v22.12.0，ccusage 20.0.0", out)
            self.assertTrue(os.access(os.path.join(place, "bin", "node"), os.X_OK), place)
            self.assertEqual([n for n in os.listdir(os.path.dirname(place)) if n.endswith(".part")], [], "the download is gone")
            os.remove(os.path.join(self.home, "curl.args"))
            rc, out, args, _ = self.node(system, machine)
            self.assertEqual((rc, args), (0, ""), "there already: nothing fetched")
            shutil.rmtree(os.path.join(self.home, ".local"))

    def test_a_download_that_fails_and_a_system_not_known_say_so(self):
        rc, out, args, place = self.node("Linux", "aarch64", curl_ok=False)
        self.assertEqual(rc, 1)
        self.assertIn("下載 node 失敗：https://nodejs.org/dist/v22.12.0/node-v22.12.0-linux-arm64.tar.gz", out)
        self.assertFalse(os.path.exists(os.path.dirname(place)) and os.listdir(os.path.dirname(place)), "nothing left")
        os.remove(os.path.join(self.home, "curl.args"))
        rc, out, args, _ = self.node("FreeBSD", "x86_64")
        self.assertEqual((rc, args), (1, ""))
        self.assertIn("不支援的系統 FreeBSD", out)


if __name__ == "__main__":
    unittest.main()
