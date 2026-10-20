"""ops/install.py (the installer behind install.sh, stage 3a.9): the quick parts, in a throwaway home. The whole run
in fake homes is tests/install/acceptance.py (about a minute, not run by deploy.sh)."""
import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))))
from altention import testing as _testing  # noqa: E402
_testing.guard_home()  # tests never touch the real home (altention/testing.py)
import builtins
import fcntl
import io
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import install  # noqa: E402

ENGINE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
LINE = re.compile(r"^(ok|changed|skip|fail|todo|unknown) [a-z0-9][a-z0-9.-]* .+$")


def shell(folder, body):
    """A stand-in for the user's shell (core.path runs $SHELL -l -i -c …): `body` is what it does."""
    path = os.path.join(folder, "殼 sh")
    with open(path, "w") as f:
        f.write("#!/bin/sh\n" + body)
    os.chmod(path, stat.S_IRWXU)
    return path


class Base(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="家 install ")
        # never the real user's shell: its rc files would be read; this one has ~/.local/bin on its PATH
        sh = shell(self.home, 'echo noise; echo %s; echo "PATH=$HOME/.local/bin:/usr/bin"\n' % install.PATH_MARK)
        self.env = mock.patch.dict(os.environ, {"HOME": self.home, "ALT_INSTANCE": "", "XDG_STATE_HOME": "", "SHELL": sh})
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

    def test_the_step_lines_go_into_the_log_too(self):
        r, _ = self.run_()
        with redirect_stdout(io.StringIO()) as o:
            r.say("todo", "a.c", "do  this")
        with open(r.log_path) as f:
            self.assertIn("todo a.c do this\n", f.read())
        self.assertEqual(o.getvalue(), "todo a.c do this\n")

    def test_discord_written_goes_on_to_the_whole_install(self):
        for rc in (0, 2):  # the install's exit status is the one passed on
            with mock.patch.object(install, "ask_discord", return_value=0), \
                    mock.patch.object(install, "install", return_value=rc) as m, redirect_stdout(io.StringIO()) as o:
                self.assertEqual(install.main(["discord"]), rc)
            m.assert_called_once_with([], after_discord=True)
            self.assertIn("接著安裝", o.getvalue())

    def test_discord_not_written_stops_before_the_install(self):
        with mock.patch.object(install, "ask_discord", return_value=1), \
                mock.patch.object(install, "install", return_value=0) as m, redirect_stdout(io.StringIO()) as o:
            self.assertEqual(install.main(["discord"]), 1)
        m.assert_not_called()
        self.assertNotIn("接著安裝", o.getvalue())

    def test_after_discord_an_install_stopped_before_its_log_says_the_last_line(self):
        """#code-review of 93e8008: stopped at the lock (or the engine, the instance), no core.done was said and
        last.log was another run's; the user waits for 安裝跑完了 or 安裝沒跑完."""
        folder = os.path.join(self.home, ".local", "state", "altention", "install")
        os.makedirs(folder)
        held = open(os.path.join(folder, "lock"), "w")
        self.addCleanup(held.close)
        fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with mock.patch("altention.engine_id.refusal", return_value=None), \
                mock.patch.object(install.config, "instance_dir", return_value=self.home), \
                redirect_stdout(io.StringIO()) as o:
            self.assertEqual(install.install([], after_discord=True), 1)
        lines = o.getvalue().splitlines()
        self.assertTrue(lines[-2].startswith("fail core.lock "), lines)
        self.assertTrue(lines[-1].startswith("fail core.done 安裝沒跑完：core.lock 沒成功"), lines)
        self.assertIn("last.log 不是這次的", lines[-1])
        with mock.patch("altention.engine_id.refusal", return_value="這是 worktree"), \
                mock.patch.object(install.config, "instance_dir", return_value=self.home), \
                redirect_stdout(io.StringIO()) as o:
            self.assertEqual(install.install([], after_discord=True), 1)
        self.assertEqual(o.getvalue().splitlines()[-1].split("：")[0], "fail core.done 安裝沒跑完")
        with mock.patch("altention.engine_id.refusal", return_value=None), \
                mock.patch.object(install.config, "instance_dir", return_value=self.home), \
                redirect_stdout(io.StringIO()) as o:  # ./install.sh itself: as before, the one line
            self.assertEqual(install.main([]), 1)
        self.assertEqual(len(o.getvalue().splitlines()), 1)

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

    def off(self, *names, placed=("progress",)):
        """`names` turned off here; the plugins in `placed` (one machine's) run here, so their place is known with no
        machine list and no bot: report is progress's, council runs everywhere."""
        plugins = dict((n, {"on": ["box"]}) for n in placed)
        for n in names:
            plugins.setdefault(n, {})["disabled_on"] = ["box"]
        with open(os.path.join(self.inst, "plugins.json"), "w") as f:
            json.dump({"format": 1, "plugins": plugins}, f)

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
        self.assertIn("changed core.skills skill 連結：report、tour；關掉的：council（拿掉了 council）", out)
        self.assertIn("ok core.skills skill 連結：report、tour；關掉的：council\n", self.install(), "said again, nothing to take")
        self.off("progress")
        self.install()
        self.assertEqual((self.link("council"), self.link("report")), (os.path.join(ENGINE, "skills", "council"), None),
                         "report is progress's")
        self.off()
        self.install()
        self.assertEqual(self.link("report"), os.path.join(ENGINE, "skills", "report"))
        self.assertEqual(self.link("tour"), os.path.join(ENGINE, "skills", "tour"), "the tour belongs to no plugin: always linked")

    def test_what_is_not_this_engines_link_is_left_and_said(self):
        self.off("council", "progress")
        os.makedirs(os.path.join(self.skills, "council"))
        os.symlink("/elsewhere/report", os.path.join(self.skills, "report"))
        out = self.install()
        self.assertEqual((self.link("council"), self.link("report")), ("folder", "/elsewhere/report"))
        self.assertEqual(out.count("skip core.skills-off"), 2, out)
        self.assertIn("不是連到這份引擎的連結，沒有動", out)
        self.assertTrue(all(LINE.match(x) for x in out.splitlines() if x.strip()), out)

    def test_settings_that_cannot_be_read_make_and_take_away_nothing(self):
        """C-11: settings that cannot be read do not say council is on: a turned-off plugin's skill does not come
        back by a comma too many, and a link already there is not taken away; a skill no plugin owns is linked."""
        self.off("council")
        self.install()
        self.assertEqual(self.link("council"), None)
        with open(os.path.join(self.inst, "plugins.json"), "w") as f:
            f.write("{ not json")
        out = self.install()
        self.assertEqual((self.link("council"), self.link("report"), self.link("tour")),
                         (None, os.path.join(ENGINE, "skills", "report"), os.path.join(ENGINE, "skills", "tour")))
        self.assertIn("skip core.skills-unknown 不知道這幾個 skill 的插件在這台開不開，先沒建：council（實例設定讀不出來）", out)
        self.assertIn("ok core.skills skill 連結：report、tour\n", out, "report's link was there: kept, not said as made")
        self.assertTrue(all(LINE.match(x) for x in out.splitlines() if x.strip()), out)
        os.remove(os.path.join(self.skills, "report"))
        self.install()
        self.assertEqual(self.link("report"), None, "a missing one is not made either")
        self.off()
        out = self.install()
        self.assertEqual((self.link("council"), self.link("report")),
                         (os.path.join(ENGINE, "skills", "council"), os.path.join(ENGINE, "skills", "report")))
        self.assertNotIn("core.skills-unknown", out, "settings fixed: linked, nothing said")

    def test_a_plugin_whose_machine_is_not_known_waits_with_its_reason(self):
        self.off(placed=())  # progress runs on one machine, and no list or bot says which
        out = self.install()
        self.assertEqual((self.link("council"), self.link("report")), (os.path.join(ENGINE, "skills", "council"), None))
        self.assertIn("先沒建：report（不知道機器人在哪台", out)

    def test_plugins_that_cannot_be_told_at_all_make_nothing(self):
        from altention import plugin
        with mock.patch.object(plugin, "on_this_machine", mock.Mock(side_effect=RuntimeError("boom"))):
            got = plugin.link_skills(ENGINE)
        self.assertEqual((got["made"], got["waiting"]), ([], ["council", "report", "tour"]))
        self.assertEqual(got["why"]["tour"], "插件讀不出來（RuntimeError: boom）")
        self.assertFalse(os.path.lexists(self.skills) and os.listdir(self.skills))

    def test_the_syncs_round_only_adds_what_is_missing(self):
        """plugin.link_skills(add_only), as the sync runs it after an update brought a skill (skills/tour, manager
        2026-10-16): a missing link is made; nothing is taken away or pointed elsewhere, a turned-off plugin's skill
        is not linked, a file under the name is left."""
        from altention import plugin
        self.off("council")
        os.makedirs(self.skills)
        os.symlink("/elsewhere/report", os.path.join(self.skills, "report"))
        with open(os.path.join(self.skills, "tour"), "w") as f:
            f.write("mine\n")
        got = plugin.link_skills(ENGINE, add_only=True)
        self.assertEqual(got["made"], [])
        self.assertEqual((self.link("council"), self.link("report")), (None, "/elsewhere/report"))
        os.remove(os.path.join(self.skills, "tour"))
        os.symlink(os.path.join(ENGINE, "skills", "council"), os.path.join(self.skills, "council"))
        got = plugin.link_skills(ENGINE, add_only=True)
        self.assertEqual(got["made"], ["tour"])
        self.assertEqual(self.link("tour"), os.path.join(ENGINE, "skills", "tour"))
        self.assertEqual(self.link("council"), os.path.join(ENGINE, "skills", "council"), "taking away is install.sh's")
        self.assertEqual(plugin.link_skills(ENGINE, add_only=True)["made"], [], "the next round finds nothing to do")
        os.remove(os.path.join(self.skills, "council"))
        self.off()
        self.assertEqual(plugin.link_skills(ENGINE, add_only=True)["made"], ["council"], "turned on again: linked")
        os.remove(os.path.join(self.skills, "report"))
        self.off(placed=())
        self.assertEqual(plugin.link_skills(ENGINE, add_only=True)["made"], [],
                         "report is progress's, whose place is not known here (no machine list, no bot): install.sh's")

    def test_the_syncs_round_with_settings_it_cannot_read_links_only_what_no_plugin_owns(self):
        from altention import plugin
        with open(os.path.join(self.inst, "plugins.json"), "w") as f:
            f.write("{ not json")
        got = plugin.link_skills(ENGINE, add_only=True)
        self.assertEqual(got["made"], ["tour"], "council and report wait: their plugin may be turned off here")
        self.assertEqual((self.link("council"), self.link("report")), (None, None))

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


class PathCheck(Base):
    """C-13: ~/.local/bin on the PATH of the user's shell, as a new terminal starts it; only said, never written
    (the rc file is the user's: manager 2026-10-17)."""

    def check(self, body=None, name="zsh", writes=False, **kw):
        if body is not None:
            folder = os.path.join(self.home, "bin-" + name)
            os.makedirs(folder, exist_ok=True)
            path = os.path.join(folder, name)
            os.rename(shell(folder, body), path)
            os.environ["SHELL"] = path
        r, out = self.run_()  # (its log under ~/.local/state)
        rcs = sorted(os.listdir(self.home))
        with redirect_stdout(out):
            install.path_check(r, **kw)
        if not writes:
            self.assertEqual(sorted(os.listdir(self.home)), rcs, "no rc file written")
        lines = [x for x in out.getvalue().splitlines() if " core.path " in x]
        self.assertEqual(len(lines), 1, out.getvalue())
        self.assertTrue(LINE.match(lines[0]), lines[0])
        return lines[0]

    def test_on_the_path(self):
        self.assertTrue(self.check().startswith("ok core.path %s/.local/bin 在" % self.home))
        line = self.check('echo %s; echo "PATH=/usr/bin:~/.local/bin/"\n' % install.PATH_MARK)
        self.assertTrue(line.startswith("ok "), line)

    def test_not_on_it_says_which_file_and_line(self):
        line = self.check('echo %s; echo PATH=/usr/bin:/bin; echo PATH_X=%s/.local/bin\n' % (install.PATH_MARK, self.home))
        self.assertEqual(line, "todo core.path %s/.local/bin 不在 zsh 的 PATH 裡，終端機打 altention、claude 會找不到："
                               "在 ~/.zshrc 加一行 export PATH=\"$HOME/.local/bin:$PATH\"，再開一個新的終端機" % self.home)
        line = self.check('echo %s; echo PATH=/usr/bin\n' % install.PATH_MARK, name="fish")
        self.assertIn("在 ~/.config/fish/config.fish 加一行 fish_add_path $HOME/.local/bin", line)
        line = self.check('echo %s; echo PATH=/usr/bin\n' % install.PATH_MARK, name="ksh")
        self.assertIn("在 ~/.profile 加一行", line)

    def test_what_the_shell_made_in_home_is_said(self):  # zsh's compinit writes ~/.zcompdump (manager 2026-10-17)
        body = 'touch "$HOME/.zcompdump"; echo %s; echo PATH=$HOME/.local/bin\n' % install.PATH_MARK
        line = self.check(body, writes=True)
        self.assertTrue(line.startswith("ok core.path "), line)
        self.assertTrue(line.endswith("在 zsh 的 PATH 裡；檢查 PATH 時 zsh 建了 ~/.zcompdump"), line)
        self.assertTrue(os.path.isfile(os.path.join(self.home, ".zcompdump")), "left: a new terminal makes it too")
        self.assertNotIn("建了", self.check(body), "already there: nothing to say")
        line = self.check('touch "$HOME/.zcompdump-x"; echo %s; echo PATH=/usr/bin\n' % install.PATH_MARK, writes=True)
        self.assertTrue(line.startswith("todo ") and line.endswith("；檢查 PATH 時 zsh 建了 ~/.zcompdump-x"), line)
        line = self.check('touch "$HOME/.zcompdump-y"; exit 3\n', writes=True)
        self.assertTrue(line.startswith("skip ") and line.endswith("；檢查 PATH 時 zsh 建了 ~/.zcompdump-y"), line)

    def test_something_the_rc_file_leaves_running_does_not_hold_it(self):  # #code-review on 5601ddb
        bg = os.path.join(self.home, "bin-zsh", "bg")
        t = time.time()
        line = self.check('sleep 20 & echo $! > "%s"; echo %s; echo PATH=$HOME/.local/bin\n'
                          % (bg, install.PATH_MARK), timeout=10)
        took = time.time() - t
        with open(bg) as f:  # what the stand-in left running goes with the test
            pid = int(f.read())
        self.addCleanup(lambda: subprocess.run(["kill", "-9", str(pid)], stderr=subprocess.DEVNULL))
        self.assertLess(took, 5)
        self.assertTrue(line.startswith("ok core.path "), line)

    def test_a_path_printed_before_the_mark_does_not_count(self):  # an rc file that prints something of its own
        line = self.check('echo PATH=%s/.local/bin; echo %s; echo PATH=/usr/bin\n' % (self.home, install.PATH_MARK))
        self.assertTrue(line.startswith("todo "), line)

    def test_a_shell_it_cannot_read_is_said_never_a_failure(self):
        for body, why in (("exit 3\n", "沒有印出 PATH（結束碼 3）"), ("sleep 30\n", "超過 1 秒沒結束")):
            line = self.check(body, timeout=1)
            self.assertTrue(line.startswith("skip core.path 讀不出你的 shell 的 PATH（"), line)
            self.assertIn(why, line)
            self.assertIn("加一行 export PATH=", line)
        os.environ["SHELL"] = os.path.join(self.home, "沒有這個殼")
        self.assertIn("跑不起來", self.check())
        os.environ["SHELL"] = ""
        self.assertIn("（沒有 SHELL）", self.check())

    def test_apply_s_run_starts_no_shell(self):
        seen = os.path.join(self.home, "bin-zsh", "started")
        with mock.patch.dict(os.environ, {"ALT_NO_PATH_CHECK": "1"}):
            line = self.check('touch "%s"; echo %s; echo PATH=/usr/bin\n' % (seen, install.PATH_MARK))
        self.assertTrue(line.startswith("skip core.path apply 跑的安裝不檢查 PATH"), line)
        self.assertFalse(os.path.exists(seen))
        with mock.patch.dict(os.environ, {"ALT_NO_PATH_CHECK": "0"}):
            self.assertTrue(self.check().startswith("ok core.path "), "only 1 counts: Base's shell asked")

    def test_the_shell_is_started_as_a_new_terminal_would(self):
        line = self.check('echo %s; echo "PATH=$*" | tr " " ":"; read x && echo PATH=$HOME/.local/bin\n' % install.PATH_MARK)
        self.assertTrue(line.startswith("todo "), "stdin is /dev/null: %s" % line)
        seen = os.path.join(self.home, "bin-zsh", "args")
        self.check('echo "$@" > "%s"; echo %s; echo PATH=/usr/bin\n' % (seen, install.PATH_MARK))
        with open(seen) as f:
            self.assertEqual(f.read(), "-l -i -c echo %s; env\n" % install.PATH_MARK)


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


class FakeDiscord:
    """The two Discord endpoints the installer reads, on 127.0.0.1: one token is good, the app and the bot's servers
    are what the test sets; every request is kept (path and headers) to look at afterwards."""
    TOK = "FAKEFAKEFAKE.not-a.real-token"

    def __init__(self):
        import http.server
        import threading
        fake = self
        self.app = {"id": "100000000000000010", "name": "testbot", "bot_public": False, "bot_require_code_grant": False,
                    "flags": 1 << 19, "owner": {"id": "100000000000000001", "username": "alice"}, "team": None,
                    "integration_types_config": {"0": {}, "1": {}}}
        self.servers = []          # what /users/@me/guilds answers now
        self.later = []            # answers from its second call on, one per call (the last one stays)
        self.limited = 0           # how many 429s to give first
        self.redirect = None       # a URL to send every call on to (302)
        self.retry_after = 0.01    # what a 429 asks to wait
        self.guild_codes = []      # statuses to answer the next calls for the servers with, one each
        self.seen = []

        class H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                fake.seen.append((self.path, dict(self.headers)))
                if fake.redirect:
                    self.send_response(302)
                    self.send_header("Location", fake.redirect + self.path)
                    self.send_header("Content-Length", "0")
                    return self.end_headers()
                if fake.limited:
                    fake.limited -= 1
                    return self.answer(429, {"retry_after": fake.retry_after})
                if self.headers.get("Authorization") != "Bot " + fake.TOK:
                    return self.answer(401, {"message": "401: Unauthorized"})
                if self.path == "/api/v10/oauth2/applications/@me":
                    return self.answer(200, fake.app)
                if self.path.startswith("/api/v10/users/@me/guilds"):
                    if fake.guild_codes:
                        return self.answer(fake.guild_codes.pop(0), {"message": "no"})
                    if fake.later and sum(p.startswith("/api/v10/users/@me/guilds") for p, _ in fake.seen) > 1:
                        fake.servers = fake.later.pop(0) if len(fake.later) > 1 else fake.later[0]
                    return self.answer(200, [{"id": i, "name": n} for i, n in fake.servers])
                self.answer(404, {})

            def answer(self, code, body):
                data = json.dumps(body).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.base = "http://127.0.0.1:%d/api/v10" % self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


class AskDiscord(Base):
    """install.sh discord: the token typed on the keyboard, the rest asked of a fake Discord; written 600, never
    shown; nothing written unless all of it is known."""
    TOK = FakeDiscord.TOK
    OWNER, GUILD = "100000000000000001", "100000000000000002"

    def setUp(self):
        super().setUp()
        # the fake Discord is on this machine: a proxy set in the developer's environment must not get the calls.
        # Taken out under Base's patch of os.environ, which puts them back (a patch of its own, undone after
        # Base's, left the throwaway HOME behind for the tests that come later).
        for k in [k for k in os.environ if k.lower() in ("http_proxy", "https_proxy", "all_proxy", "no_proxy")]:
            del os.environ[k]
        self.path = os.path.join(self.home, ".config", "dcbot", "env")
        self.fake = FakeDiscord()
        self.addCleanup(self.fake.close)
        self.fake.servers = [(self.GUILD, "my server")]
        self.slept = []

    def ask(self, answers, secrets=None, interactive=True, base=None):
        answers, secrets = list(answers), list(secrets if secrets is not None else [self.TOK])
        self.asked = []
        t = [0.0]

        def line(prompt):
            self.asked.append(prompt)
            return answers.pop(0)

        def sleep(s):
            self.slept.append(s)
            t[0] += s
        out = io.StringIO()
        with redirect_stdout(out):
            rc = install.ask_discord(secret=lambda prompt: secrets.pop(0), line=line, interactive=interactive,
                                     base=base or self.fake.base, sleep=sleep, clock=lambda: t[0])
        return rc, out.getvalue()

    def ask_with(self, line):
        out = io.StringIO()
        with redirect_stdout(out):
            rc = install.ask_discord(secret=lambda p: self.TOK, line=line, interactive=True, base=self.fake.base,
                                     sleep=lambda s: None, clock=lambda: 0.0)
        return rc, out.getvalue()

    def lines(self):
        with open(self.path) as f:
            return f.read().splitlines()

    def test_one_server_written_600_in_a_700_folder_and_never_shown(self):
        rc, out = self.ask([])
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.lines(), ["DISCORD_TOKEN=" + self.TOK, "OWNER_ID=" + self.OWNER, "GUILD_ID=" + self.GUILD])
        self.assertEqual(stat.S_IMODE(os.stat(self.path).st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(os.stat(os.path.dirname(self.path)).st_mode), 0o700)
        self.assertNotIn(self.TOK, out)
        self.assertIn("changed dcbot.token", out)
        self.assertIn("接下來會問你機器人的 token（打字時看不到）", out.splitlines()[0])
        self.assertNotIn("oauth2/authorize", out, "already in one server: no link to click")
        self.assertIn("my server", out)
        self.assertEqual(os.listdir(os.path.dirname(self.path)), ["env"], "no temporary file left")
        self.assertTrue(self.fake.seen)
        for path, headers in self.fake.seen:
            self.assertEqual(headers.get("Authorization"), "Bot " + self.TOK)
            self.assertTrue(headers.get("User-Agent", "").startswith("DiscordBot ("), headers)

    def test_no_server_yet_prints_the_link_and_waits_for_the_bot(self):
        self.fake.servers, self.fake.later = [], [[], [], [(self.GUILD, "new")]]
        rc, out = self.ask([])
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.lines()[2], "GUILD_ID=" + self.GUILD)
        link = [x for x in out.splitlines() if x.startswith("https://discord.com/oauth2/authorize?")]
        self.assertEqual(len(link), 1, out)
        from urllib.parse import parse_qs, urlsplit
        q = parse_qs(urlsplit(link[0]).query)
        self.assertEqual(q["client_id"], ["100000000000000010"])
        self.assertEqual(q["scope"], ["bot applications.commands"])
        self.assertEqual(q["permissions"], ["2252128378809552"], "the 14 permissions of the install guide")
        self.assertEqual(q["integration_type"], ["0"])
        self.assertEqual(self.slept, [3, 3, 3], "a look every 3 seconds until the bot is in")

    def test_the_14_permissions(self):
        import discordapp
        bits = dict(discordapp.PERMISSIONS)
        self.assertEqual(len(bits), 14)
        self.assertEqual(discordapp.PERMISSION_BITS, sum(1 << b for b in bits.values()))
        self.assertEqual((bits["PIN_MESSAGES"], bits["MANAGE_THREADS"], bits["VIEW_CHANNEL"]), (51, 34, 10))

    def test_waiting_too_long_asks_and_q_writes_nothing(self):
        self.fake.servers = []
        rc, out = self.ask(["q"])
        self.assertEqual(rc, 1, out)
        self.assertIn("todo dcbot.token 停了，什麼都沒寫", out)
        self.assertIn("10 分鐘了", self.asked[-1])
        self.assertAlmostEqual(sum(self.slept), 600, delta=3)
        self.assertFalse(os.path.exists(self.path))

    def test_waiting_too_long_enter_waits_again(self):
        self.fake.servers, self.fake.later = [], [[]] * 205 + [[(self.GUILD, "late")]]
        rc, out = self.ask([""])
        self.assertEqual(rc, 0, out)
        self.assertEqual(out.count("等機器人進伺服器"), 2)
        self.assertEqual(self.lines()[2], "GUILD_ID=" + self.GUILD)

    def test_several_servers_asks_which(self):
        self.fake.servers = [("100000000000000003", "old"), (self.GUILD, "new")]
        rc, out = self.ask(["9", "x", "2"])
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.lines()[2], "GUILD_ID=" + self.GUILD)
        self.assertIn("1. old", out)
        self.assertEqual(out.count("要 1 到 2 的號碼"), 2)

    def test_a_team_app_asks_who_the_owner_is(self):
        """The app's owner field is then the team, not a person: the accepted members, team owner first."""
        self.fake.app["owner"] = {"id": "100000000000000099", "username": "team100000000000000098", "flags": 1024}
        self.fake.app["team"] = {"id": "100000000000000098", "owner_user_id": "100000000000000005", "members": [
            {"membership_state": 2, "role": "admin", "user": {"id": "100000000000000004", "username": "bob"}},
            {"membership_state": 1, "role": "developer", "user": {"id": "100000000000000006", "username": "mallory"}},
            {"membership_state": 2, "role": "", "user": {"id": "100000000000000005", "username": "carol"}}]}
        rc, out = self.ask(["2"])
        self.assertEqual(rc, 0, out)
        self.assertIn("1. carol（團隊擁有者）", out)
        self.assertIn("2. bob", out)
        self.assertNotIn("mallory", out, "only members who accepted")
        self.assertEqual(self.lines()[1], "OWNER_ID=100000000000000004")
        rc, out = self.ask(["y", ""])
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.lines()[1], "OWNER_ID=100000000000000005", "Enter is the team owner")

    def test_a_team_of_one_asks_nothing(self):
        self.fake.app["team"] = {"owner_user_id": "100000000000000005", "members": [
            {"membership_state": 2, "user": {"id": "100000000000000005", "username": "carol"}}]}
        rc, out = self.ask([])
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.lines()[1], "OWNER_ID=100000000000000005")

    def test_settings_still_to_change_are_said_and_checked_again(self):
        self.fake.app.update(flags=0, bot_public=True, bot_require_code_grant=True, integration_types_config={"1": {}})
        asked = []

        def fixed(prompt):
            asked.append(prompt)
            self.fake.app.update(flags=1 << 19, bot_public=False, bot_require_code_grant=False,
                                 integration_types_config={"0": {}})
            return ""
        rc, out = self.ask_with(fixed)
        self.assertEqual(rc, 0, out)
        for word in ("Message Content Intent", "Public Bot", "Requires OAuth2 Code Grant", "Guild Install"):
            self.assertIn(word, out.split("應用程式：")[0].split("還要改")[1])
        self.assertEqual(len(asked), 1, "checked again once, then nothing left")
        self.assertEqual(self.lines()[2], "GUILD_ID=" + self.GUILD)
        self.fake.app["bot_public"] = True
        rc, out = self.ask(["y", "q"])
        self.assertEqual(rc, 1)
        self.assertIn("todo dcbot.token 停了", out)
        self.assertEqual(self.lines()[0], "DISCORD_TOKEN=" + self.TOK, "q leaves the file as it was")

    def test_a_wrong_token_is_asked_again_and_three_write_nothing(self):
        rc, out = self.ask([], secrets=["WRONG.WRONG.WRONG", self.TOK])
        self.assertEqual(rc, 0, out)
        self.assertIn("Discord 說這個 token 不對", out)
        self.assertNotIn("WRONG", out)
        os.remove(self.path)
        self.fake.seen = []
        rc, out = self.ask([], secrets=["", "abcdef", "SECRETPART.x.y.z"])
        self.assertEqual(rc, 1, out)
        self.assertIn("沒有讀到任何字", out)
        self.assertIn("讀到 6 個字、0 個點（", out)
        self.assertIn("讀到 16 個字、3 個點（", out)
        self.assertIn("fail dcbot.token 三次都沒拿到", out)
        self.assertNotIn("SECRETPART", out)
        self.assertNotIn("abcdef", out)
        self.assertFalse(os.path.exists(self.path))
        self.assertEqual(self.fake.seen, [], "a token of the wrong shape is never sent")

    def test_too_many_requests_is_waited_out(self):
        self.fake.limited = 2
        rc, out = self.ask([])
        self.assertEqual(rc, 0, out)
        self.assertEqual(len(self.slept), 2)

    def test_a_redirect_is_not_followed(self):
        """urllib would follow it with the Authorization header, to another host too (#code-review-2 2026-10-16)."""
        other = FakeDiscord()
        self.addCleanup(other.close)
        self.fake.redirect = other.base.replace("127.0.0.1", "localhost").rsplit("/api/v10", 1)[0]
        rc, out = self.ask([])
        self.assertEqual(rc, 1, out)
        self.assertIn("fail dcbot.token Discord 回了轉址（HTTP 302），沒有跟過去；什麼都沒寫", out)
        self.assertEqual(other.seen, [], "nothing sent on")
        self.assertFalse(os.path.exists(self.path))

    def test_a_folder_made_before_is_tightened_to_700(self):
        folder = os.path.dirname(self.path)
        os.makedirs(folder)
        os.chmod(folder, 0o755)
        rc, out = self.ask([])
        self.assertEqual(rc, 0, out)
        self.assertEqual(stat.S_IMODE(os.stat(folder).st_mode), 0o700)
        os.chmod(folder, 0o311)
        rc, out = self.ask(["y"])
        self.assertEqual(rc, 0, out)
        self.assertEqual(stat.S_IMODE(os.stat(folder).st_mode), 0o300, "only tightened: no bit the owner lacked is added")
        os.chmod(folder, 0o700)

    def test_trouble_while_waiting_is_said_once_and_waited_out(self):
        """A 5xx or no network while the bot is awaited does not end the command (manager 2026-10-16)."""
        self.fake.servers, self.fake.later = [], [[], [(self.GUILD, "s")]]
        self.fake.guild_codes = [500, 500, 502]
        rc, out = self.ask([])
        self.assertEqual(rc, 0, out)
        self.assertEqual(out.count("暫時連不上 Discord（Discord 回了 HTTP 500），繼續等"), 1, "said once, not every 3 s")
        self.assertEqual(out.count("暫時連不上 Discord（Discord 回了 HTTP 502），繼續等"), 1, out)
        self.assertIn("又連上 Discord 了", out)
        self.assertIn("oauth2/authorize", out, "the first look failed: the link is printed all the same")
        self.assertEqual(self.lines()[2], "GUILD_ID=" + self.GUILD)

    def test_only_401_and_403_end_the_waiting(self):
        for code, said in ((401, "Discord 中途說 token 不對了"), (403, "Discord 回了 HTTP 403（不准）；什麼都沒寫")):
            self.fake.servers, self.fake.guild_codes = [], [500, code]
            rc, out = self.ask([])
            self.assertEqual(rc, 1, out)
            self.assertIn("fail dcbot.token " + said, out)
            self.assertFalse(os.path.exists(self.path))

    def test_429_waits_as_long_as_asked_up_to_ten_minutes(self):
        self.fake.limited, self.fake.retry_after = 1, 45
        rc, out = self.ask([])
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.slept, [45], "no 30 seconds cap")
        self.fake.limited, self.fake.retry_after = 1, 700
        rc, out = self.ask(["y"])
        self.assertEqual(rc, 1, out)
        self.assertIn("fail dcbot.token Discord 說請求太多，要等 12 分鐘才能再查（429）", out)

    def test_discord_unreachable_writes_nothing(self):
        import socket
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()
        rc, out = self.ask([], base="http://127.0.0.1:%d/api/v10" % port)
        self.assertEqual(rc, 1)
        self.assertIn("fail dcbot.token 連不上 Discord", out)
        self.assertNotIn(self.TOK, out)
        self.assertFalse(os.path.exists(self.path))

    def test_from_anything_but_a_terminal_it_refuses(self):
        rc, out = self.ask([], interactive=False)
        self.assertEqual(rc, 1)
        self.assertIn("fail dcbot.token", out)
        self.assertFalse(os.path.exists(os.path.dirname(self.path)))
        self.assertEqual(self.fake.seen, [])
        with mock.patch.object(sys.stdin, "isatty", return_value=False), redirect_stdout(io.StringIO()) as o:
            self.assertEqual(install.main(["discord"]), 1)
        self.assertIn("fail dcbot.token", o.getvalue())
        with redirect_stdout(io.StringIO()) as o:
            self.assertEqual(install.main(["--token"]), 1)
        self.assertIn("fail core.args 不認得 --token", o.getvalue())

    def test_stopped_halfway_writes_nothing(self):
        self.fake.servers = [("100000000000000003", "a"), (self.GUILD, "b")]

        def stop(prompt):
            raise KeyboardInterrupt
        rc, out = self.ask_with(stop)
        self.assertEqual(rc, 1)
        self.assertIn("什麼都沒寫", out)
        self.assertFalse(os.path.exists(self.path))

    def test_an_existing_file_is_kept_unless_asked_and_its_other_lines_stay(self):
        os.makedirs(os.path.dirname(self.path))
        with open(self.path, "w") as f:
            f.write("# 我的設定\nDISCORD_TOKEN=OLD.OLD.OLD\nMODEL=opus\nOWNER_ID=100000000000000009\n")
        os.chmod(self.path, 0o600)
        before = self.lines()
        rc, out = self.ask([""])
        self.assertEqual((rc, self.lines()), (0, before), "Enter keeps it")
        self.assertIn("ok dcbot.token", out)
        self.assertEqual(self.fake.seen, [])
        rc, out = self.ask(["y"])
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
        rc, out = self.ask(["y"])
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.lines(), ["DISCORD_TOKEN=" + self.TOK, "OWNER_ID=" + self.OWNER, "GUILD_ID=" + self.GUILD,
                                        "exported_TOKEN=keep", "export MODEL=opus"])
        with open(self.path) as f:
            self.assertNotIn("OLD", f.read())

    @unittest.skipUnless(os.path.isdir("/proc/self"), "reads /proc")
    def test_on_a_real_terminal_the_token_is_in_no_argument_environment_or_output(self):
        """Run under a pseudo-terminal, as the user does: getpass reads it from the terminal; while the command
        waits for the bot, its command line and environment hold no token, and nothing it printed does."""
        import pty
        import select
        import time
        self.fake.servers, self.fake.later = [], [[]] * 100000
        code = ("import sys, time; sys.path.insert(0, %r); import install; "
                "sys.exit(install.ask_discord(base=sys.argv[1], sleep=lambda s: time.sleep(0.05)))"
                % os.path.join(ENGINE, "ops"))
        pid, fd = pty.fork()
        if pid == 0:  # the child: nothing but exec (an exception here must not run the rest of the tests)
            try:
                os.execv(sys.executable, [sys.executable, "-c", code, self.fake.base])
            finally:
                os._exit(127)
        got = [b""]

        def read_until(word, limit=20):
            end = time.time() + limit
            while word not in got[0].decode("utf-8", "replace") and time.time() < end:
                if select.select([fd], [], [], 0.2)[0]:
                    try:
                        got[0] += os.read(fd, 4096)
                    except OSError:
                        break
            return word in got[0].decode("utf-8", "replace")
        try:
            # The prompt itself, not just "token（" (the line before it says that too): getpass turns the echo off,
            # flushing what was typed, before it prints the prompt, so a token written earlier is echoed and lost.
            self.assertTrue(read_until(install.TOKEN_PROMPT), got[0])
            os.write(fd, (self.TOK + "\n").encode())
            self.assertTrue(read_until("等機器人進伺服器"), got[0])
            for what in ("cmdline", "environ"):
                with open("/proc/%d/%s" % (pid, what), "rb") as f:
                    self.assertNotIn(self.TOK.encode(), f.read(), what)
            self.fake.later = [[(self.GUILD, "s")]]
            self.assertTrue(read_until("changed dcbot.token"), got[0])
        finally:
            try:
                os.kill(pid, 9)
            except OSError:
                pass
            os.waitpid(pid, 0)
            os.close(fd)
        self.assertNotIn(self.TOK, got[0].decode("utf-8", "replace"), "not echoed, not printed")
        self.assertEqual(self.lines()[0], "DISCORD_TOKEN=" + self.TOK)


class NoBot(Base):
    def test_no_bot_anywhere_is_said_with_the_command(self):
        r, out = self.run_()
        with mock.patch.object(install.config, "bot_machine", return_value=(None, None, False)), redirect_stdout(out):
            install.token(r, False)
        self.assertRegex(out.getvalue(), r"todo dcbot.token .*install.sh discord")
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


class Setup(Base):
    """C-48: a new installation's instance waits for #setup (setup.done false, the permission mode set), progress
    is held until then, and #setup's folder lets only setup.py (and the update subcommands) through."""

    def instance(self, **settings):
        inst = os.path.join(self.home, "實例")
        os.makedirs(inst, exist_ok=True)
        with open(os.path.join(inst, "plugins.json"), "w") as f:
            json.dump(dict({"format": 1}, **settings), f)
        return mock.patch.dict(os.environ, {"ALT_INSTANCE": inst})

    def test_a_new_installations_instance(self):
        import instance as ops_instance
        eng = os.path.join(self.home, "altention", "engine")
        os.makedirs(os.path.join(eng, "altention"))
        with mock.patch.object(ops_instance, "ENGINE", eng), redirect_stdout(io.StringIO()):
            self.assertEqual(ops_instance.main(["init", "--permission", "manual"]), 0)
            self.assertEqual(ops_instance.main(["init", "--permission", "bypassPermissions"]), 2, "never written as is")
        d = install.config.read_json(os.path.join(self.home, "altention", "instance", "plugins.json"))
        self.assertEqual((d["setup"], d["dcbot"]), ({"done": False}, {"default_permission": "manual"}))
        self.assertNotIn("setup", ops_instance.plugins_skeleton(), "seed and the rest: no setup to ask")

    def test_init_is_told_the_permission_this_claude_code_has(self):
        for modes, want in ((["auto", "manual"], "auto"), (["acceptEdits", "default"], "manual"), (None, "auto")):
            with self.subTest(modes=modes):
                r, out = self.run_()
                seen = []
                fake = mock.Mock()
                fake.claude_modes.return_value = modes
                with mock.patch.object(install, "setup_cmd", return_value=fake), \
                        mock.patch.object(r, "script", side_effect=lambda label, argv: seen.append(argv) or (1, "no")), \
                        redirect_stdout(out):
                    install.find_instance(r, False)
                self.assertEqual(seen[0][-2:], ["--permission", want])

    def test_progress_waits_for_setup(self):
        for setup, held in (({"done": False}, ["progress"]), ({"done": True}, []), (None, [])):
            with self.subTest(setup=setup), self.instance(**({"setup": setup} if setup else {})):
                r, out = self.run_()
                holds = []
                with redirect_stdout(out):
                    install.hold_until_setup(r, holds)
                self.assertEqual(holds, held)
                self.assertEqual("skip core.setup" in out.getvalue(), bool(held))

    def test_the_last_line_points_to_setup(self):
        with self.instance(setup={"done": False}):
            r, out = self.run_()
            with redirect_stdout(out):
                install.done(r, install.Steps(r), "安裝")
        self.assertRegex(out.getvalue(), r"(?m)^ok core.done 安裝跑完了：到 Discord 的 #setup 把設定做完$")

    def test_what_is_missing_is_said(self):
        r, out = self.run_()
        with mock.patch.object(install.shutil, "which", return_value=None), \
                mock.patch.object(install.subprocess, "run", return_value=install.subprocess.CompletedProcess([], 1)), \
                redirect_stdout(out):
            install.needs(r)
        line = out.getvalue()
        self.assertTrue(line.startswith("todo core.needs "), line)
        for word in ("tmux", "Claude Code", "python3-venv"):
            self.assertIn(word, line)

    def test_a_local_only_skill_is_never_linked_globally(self):
        from altention import plugin
        eng = os.path.join(self.home, "引擎")
        for name in ("a", "setup"):
            os.makedirs(os.path.join(eng, "skills", name))
        open(os.path.join(eng, "skills", "setup", plugin.LOCAL_ONLY), "w").close()
        with redirect_stdout(io.StringIO()):
            got = plugin.link_skills(eng)
        self.assertEqual((got["names"], got["made"]), (["a"], ["a"]))
        self.assertFalse(os.path.lexists(os.path.join(self.home, ".claude", "skills", "setup")))

    def test_setups_folder(self):
        eng = os.path.join(self.home, "引擎")
        os.makedirs(os.path.join(eng, "skills", "setup"))
        os.makedirs(os.path.join(eng, "skills", "update"))
        folder = os.path.join(self.home, ".local", "state", "altention", "setup")
        with mock.patch.object(install, "ENGINE", eng):
            r, out = self.run_()
            with redirect_stdout(out):
                install.setup_folder(r, False)
            self.assertIn("skip core.setup-folder", out.getvalue())
            self.assertFalse(os.path.exists(folder))
            r, out = self.run_()
            with redirect_stdout(out):
                install.setup_folder(r, True)
            self.assertIn("changed core.setup-folder", out.getvalue())
            with open(os.path.join(folder, ".claude", "settings.json")) as f:
                allow = json.load(f)["permissions"]["allow"]
            self.assertEqual(allow[0], "Bash(python3 %s/ops/setup.py:*)" % eng)
            self.assertEqual(allow[1:], ["Bash(%s/bin/altention update %s:*)" % (eng, s)
                                         for s in ("check", "go", "save", "apply", "undo")])
            for name in ("setup", "update"):  # S4a: an update handed over is #setup's too
                self.assertEqual(os.readlink(os.path.join(folder, ".claude", "skills", name)),
                                 os.path.join(eng, "skills", name))
                self.assertFalse(os.path.lexists(os.path.join(self.home, ".claude", "skills", name)), "never global")
            self.assertFalse(os.path.lexists(os.path.join(self.home, ".claude", "skills", "setup")), "never global")
            self.assertEqual(oct(os.stat(folder).st_mode & 0o777), oct(0o700))
            os.makedirs(os.path.join(folder, "update-work"))
            with open(os.path.join(folder, "update-work", "x"), "w") as f:
                f.write("U chain's")
            with open(os.path.join(folder, ".claude", "settings.json"), "w") as f:
                f.write('{"permissions": {"allow": ["Bash(rm:*)"]}}')  # added by hand: written over
            r, out = self.run_()
            with redirect_stdout(out):
                install.setup_folder(r, True)
            with open(os.path.join(folder, ".claude", "settings.json")) as f:
                self.assertNotIn("rm:", f.read())
            self.assertTrue(os.path.exists(os.path.join(folder, "update-work", "x")))
            r, out = self.run_()
            with redirect_stdout(out):
                install.setup_folder(r, True)
            self.assertIn("ok core.setup-folder", out.getvalue())


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


class Backups(Base):
    """C-25 (data-safety 5): a machine set up again gets the progress repo and the secretary's folder back from the
    remotes the instance names, before their first round; never into a folder already there (manager 2026-10-17)."""

    def setUp(self):
        super().setUp()
        import test_remote
        self.tr = test_remote
        os.environ.update({"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com", "GIT_COMMITTER_NAME": "t",
                           "GIT_COMMITTER_EMAIL": "t@example.com", "ALT_DATA": os.path.join(self.home, "data")})
        self.tmp = os.path.join(self.home, "remotes")
        self.values = {}
        import remote
        self.patches = [mock.patch.object(install.config, "setting", side_effect=lambda k, *a: self.values.get(k)),
                        mock.patch.object(remote, "engine_urls", return_value=["git@github.com:alice/altention.git"])]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        super().tearDown()

    def backups(self, on=("progress", "planner")):
        r, _ = self.run_()
        with redirect_stdout(io.StringIO()) as o:
            install.backups(r, list(on))
        return o.getvalue().splitlines(), r

    def folder(self, name):
        return os.path.join(self.home, "data", name)

    def git(self, folder, *args):
        return subprocess.run(["git", "-C", folder] + list(args), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              universal_newlines=True)

    def test_a_folder_not_there_is_cloned_into_700(self):
        self.values = {"sync.dirs.progress.remote": self.tr.bare_with(self.tmp, "prog", ["STATUS.md"]),
                       "sync.dirs.planner.remote": self.tr.bare_with(self.tmp, "plan", ["plan.md"])}
        lines, r = self.backups()
        self.assertEqual([l.split()[:2] for l in lines], [["changed", "core.backup"]] * 2, lines)
        for name, f in (("progress", "STATUS.md"), ("planner", "plan.md")):
            self.assertTrue(os.path.isfile(os.path.join(self.folder(name), f)), name)
            self.assertEqual(stat.S_IMODE(os.stat(self.folder(name)).st_mode), 0o700, name)
        self.assertTrue(os.path.isfile(os.path.join(self.folder("planner"), ".gitignore")), "made as remind makes one")
        self.assertEqual(self.git(self.folder("planner"), "status", "--porcelain").stdout, "", "committed")
        import remote
        for name in ("progress", "planner"):
            self.assertTrue(remote.has_hook(self.folder(name)), "the pre-push hook, before anything pushes from it")
        self.assertEqual(r.code(), 0)
        lines, _ = self.backups()
        self.assertEqual([l.split()[0] for l in lines], ["ok", "ok"], "then left as it is")

    def test_an_empty_backup_comes_back_on_main(self):
        self.values = {"sync.dirs.progress.remote": self.tr.bare_with(self.tmp, "prog", []),
                       "sync.dirs.planner.remote": self.tr.bare_with(self.tmp, "plan", [])}
        self.backups()
        for name in ("progress", "planner"):
            self.assertEqual(self.git(self.folder(name), "symbolic-ref", "--short", "HEAD").stdout.strip(), "main", name)

    def head_names(self, bare, ref):
        subprocess.run(["git", "-C", bare, "symbolic-ref", "HEAD", ref], check=True)
        return bare

    def test_a_remote_whose_head_names_no_branch(self):
        """#code-review of bc09467: `git init --bare` says master, the data is on main: the clone was an empty branch
        said as "cloned back". Its main (or its only branch) is checked out tracking origin."""
        prog = self.head_names(self.tr.bare_with(self.tmp, "prog", ["STATUS.md"]), "refs/heads/master")
        plan = self.tr.bare_with(self.tmp, "plan", ["plan.md"])
        work = os.path.join(self.tmp, "plan.work")
        self.head_names(plan, "refs/heads/master")  # one branch, not main (the HEAD first: git keeps its branch)
        subprocess.run(["git", "-C", work, "branch", "-m", "main", "mine"], check=True)
        subprocess.run(["git", "-C", work, "push", "-q", "origin", "mine", ":main"], check=True)
        self.values = {"sync.dirs.progress.remote": prog, "sync.dirs.planner.remote": plan}
        lines, _ = self.backups()
        self.assertTrue(all("從備份 clone 回來" in l for l in lines), lines)
        for name, branch, f in (("progress", "main", "STATUS.md"), ("planner", "mine", "plan.md")):
            self.assertTrue(os.path.isfile(os.path.join(self.folder(name), f)), name)
            self.assertEqual(self.git(self.folder(name), "rev-parse", "--abbrev-ref", "@{u}").stdout.strip(), "origin/" + branch)
        self.assertEqual(self.git(self.folder("planner"), "rev-list", "--count", "@{u}..HEAD").stdout.strip(), "1",
                         "the .gitignore on top of the backup, nothing else")

    def test_progress_from_another_only_branch_goes_on_main(self):
        """C-43: progress pushes main (`checkout -B main`, as it makes the repo it clones): the line says so."""
        prog = self.head_names(self.tr.bare_with(self.tmp, "prog", ["STATUS.md"]), "refs/heads/master")
        work = os.path.join(self.tmp, "prog.work")
        subprocess.run(["git", "-C", work, "push", "-q", "origin", "main:master", ":main"], check=True)
        self.values = {"sync.dirs.progress.remote": prog}
        lines, _ = self.backups(on=("progress",))
        self.assertIn("遠端原本的分支是 master，之後推到 main", lines[0])
        self.assertEqual(self.git(self.folder("progress"), "symbolic-ref", "--short", "HEAD").stdout.strip(), "main")
        self.assertTrue(os.path.isfile(os.path.join(self.folder("progress"), "STATUS.md")))

    def test_several_branches_and_no_main_or_nothing_is_said_so(self):
        prog = self.tr.bare_with(self.tmp, "prog", ["STATUS.md"])
        work = os.path.join(self.tmp, "prog.work")
        self.head_names(prog, "refs/heads/master")
        subprocess.run(["git", "-C", work, "push", "-q", "origin", "main:a", "main:b", ":main"], check=True)
        self.values = {"sync.dirs.progress.remote": prog,
                       "sync.dirs.planner.remote": self.tr.bare_with(self.tmp, "plan", [])}
        lines, _ = self.backups()
        self.assertIn("認不出哪個分支是備份（有 a、b，沒有 main）", lines[0])
        self.assertIn("裡面沒有內容（空的 repo）", lines[1])
        self.assertFalse(any("從備份 clone 回來" in l for l in lines), lines)
        self.assertEqual(self.git(self.folder("progress"), "symbolic-ref", "--short", "HEAD").stdout.strip(), "main")
        self.assertEqual(subprocess.run(["git", "-C", prog, "for-each-ref", "--format=%(refname:short)"], stdout=subprocess.PIPE,
                                        universal_newlines=True).stdout.split(), ["a", "b"], "the remote's branches untouched")

    def test_a_folder_already_there_is_never_touched(self):
        """Even empty, even 755: its plugin attaches it (remind --secretary, progress' first round)."""
        self.values = {"sync.dirs.progress.remote": self.tr.bare_with(self.tmp, "prog", ["STATUS.md"]),
                       "sync.dirs.planner.remote": self.tr.bare_with(self.tmp, "plan", ["plan.md"])}
        os.makedirs(self.folder("progress"), mode=0o755)
        os.chmod(self.folder("progress"), 0o755)
        os.makedirs(self.folder("planner"))
        with open(os.path.join(self.folder("planner"), "mine.md"), "w") as f:
            f.write("mine\n")
        lines, r = self.backups()
        self.assertEqual([l.split()[0] for l in lines], ["ok", "ok"], lines)
        self.assertEqual(os.listdir(self.folder("progress")), [])
        self.assertEqual(stat.S_IMODE(os.stat(self.folder("progress")).st_mode), 0o755, "not even its mode")
        self.assertEqual(os.listdir(self.folder("planner")), ["mine.md"])

    def test_a_public_or_other_remote_is_not_cloned_and_nothing_is_left(self):
        import remote
        self.values = {"sync.dirs.progress.remote": self.tr.bare_with(self.tmp, "eng", ["altention/sync.py"]),
                       "sync.dirs.planner.remote": "https://example.com/alice/plan.git"}
        public = [("anonymous", "no", "這個 repo 是公開的（讀得到）：改成 private 再設，或換一個 private 的")]
        real = remote.check
        with mock.patch.object(remote, "alert") as told, \
                mock.patch.object(remote, "check", side_effect=lambda u, n=None: public if u.startswith("https:") else real(u, n)):
            lines, r = self.backups()
        self.assertEqual([l.split()[:2] for l in lines], [["todo", "core.backup"]] * 2, lines)
        self.assertIn("公開的", lines[1])
        self.assertIn("python3 ops/remote.py set planner", lines[1])
        self.assertFalse(os.path.exists(self.folder("progress")))
        self.assertFalse(os.path.exists(self.folder("planner")))
        self.assertEqual([c.args[0] for c in told.call_args_list], ["clone-refused"] * 2)
        self.assertEqual(r.code(), 2)

    def test_nothing_for_a_part_off_here_unset_or_in_its_own_folder(self):
        self.values = {"sync.dirs.progress.remote": self.tr.bare_with(self.tmp, "prog", ["STATUS.md"])}
        self.assertEqual(self.backups(on=("planner",))[0], [], "progress off here; planner has no remote")
        mine = os.path.join(self.home, "life")
        self.values = {"sync.dirs.planner.remote": self.tr.bare_with(self.tmp, "plan", ["plan.md"]), "planner.folder": mine}
        lines, _ = self.backups()
        self.assertEqual([l.split()[0] for l in lines], ["ok"], lines)
        self.assertFalse(os.path.exists(mine))
        self.assertFalse(os.path.exists(self.folder("planner")))

    def test_the_step_runs_after_the_plugins_and_only_with_them(self):
        steps = re.findall(r'st\.run\("([a-z.-]+)"', open(install.__file__, encoding="utf-8").read())
        self.assertLess(steps.index("core.install"), steps.index("core.backup"))
        self.assertLess(steps.index("core.backup"), steps.index("core.apply"))


if __name__ == "__main__":
    unittest.main()
