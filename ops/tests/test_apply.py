"""ops/apply.py, stage 3a.8: the remote script stops at the first step that fails and never reaches plugins.py apply
--write; a folder under an old name stops it; install scripts run with --no-cron never call crontab nor touch
settings.json; a machine whose placement is unknown is left alone while the others are
done; a single-machine plugin is added only when every other machine has been read to hold none of it (--force-move
says the old one may still run it); plugins.py held reads a crontab."""
import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))))
from altention import testing as _testing  # noqa: E402
_testing.guard_home()  # tests never touch the real home (altention/testing.py)
import contextlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import apply  # noqa: E402
import plancore  # noqa: E402
import plugins  # noqa: E402

ENGINE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
LOGGER = """#!/bin/sh
echo "$(basename "$0") $*" >> "$CALLS"
exit 1
"""


def row(name, where, machines="all"):
    return (name, "/e/" + name, {"machines": machines}, where, "")


class Rig(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.home = os.path.join(self.tmp, "home")
        os.makedirs(self.home)
        self.calls = os.path.join(self.tmp, "calls")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def fake_bin(self, names):
        """A folder of commands that only write down that they were called (and fail), first in PATH."""
        b = os.path.join(self.tmp, "bin")
        os.makedirs(b, exist_ok=True)
        for n in names:
            p = os.path.join(b, n)
            with open(p, "w") as f:
                f.write(LOGGER)
            os.chmod(p, 0o755)
        return b

    def called(self):
        if not os.path.exists(self.calls):
            return []
        with open(self.calls) as f:
            return f.read().splitlines()


class RemoteScript(Rig):
    """apply.py's remote script on a fake engine: home-pull, instance join, the bot's uninstall and the installer
    entry (install.sh) are tiny scripts that write down that they ran. The installer's own steps are its acceptance
    test's (tests/install/acceptance.py)."""
    stopped = False
    todo = False
    allow = ""

    def engine(self, fail=None, silent=()):
        """`fail` exits 3 (install.sh: 1 with a fail line); `silent` does nothing and gives 0 (a stand-in)."""
        h = os.path.join(self.tmp, "engine 引擎")
        os.makedirs(h)
        git = ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", "-C", h]
        subprocess.run(git + ["init", "-q"], check=True)
        subprocess.run(git + ["commit", "-q", "--allow-empty", "-m", "x"], check=True)  # rev-parse gives a hash
        steps = {"bin/home-pull": ("home-pull", ""), "ops/instance.py": ("instance", "ALT-STEP-OK instance-join"),
                 "dcbot/uninstall.sh": ("uninstall", "ALT-STEP-OK dcbot-uninstall"), "install.sh": ("install", "")}
        for rel, (name, mark) in steps.items():
            p = os.path.join(h, rel)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "w") as f:
                if name in silent:
                    f.write("#!/bin/sh\nexit 0\n" if not rel.endswith(".py") else "")
                elif rel.endswith(".py"):
                    f.write("import sys\nopen(%r, 'a').write(%r + ' ' + ' '.join(sys.argv[1:]) + '\\n')\n" % (self.calls, name))
                    if name == fail:
                        f.write("sys.exit(3)\n")
                    elif name == "instance":  # join, then pull: each prints its own mark
                        f.write("print('ALT-STEP-OK instance-' + sys.argv[1])\n")
                    else:
                        f.write("print(%r)\n" % mark)
                elif name == "install":
                    body = ["echo \"install $*\" >> %s" % self.calls, "echo \"${ALT_NO_PATH_CHECK-unset}\" > %s.path" % self.calls,
                            "echo 'ok core.instance 實例：x'"]
                    if name == fail:
                        body += ["echo 'fail plugin.progress progress/install.sh 失敗'", "echo 'ok core.done 安裝跑完了'", "exit 1"]
                    else:
                        if self.stopped:
                            body.append("echo 'todo dcbot.stopped supervisor 被人停著：刪掉 /x/state/stop'")
                        if self.todo:
                            body.append("echo 'todo dcbot.token 缺機器人的 token：寫進 ~/.config/dcbot/env'")
                        body += ["echo 'ok core.done 安裝跑完了'", "exit %d" % (2 if self.stopped or self.todo else 0)]
                    f.write("#!/bin/sh\n" + "\n".join(body) + "\n")
                else:
                    f.write("#!/bin/sh\necho \"%s $*\" >> %s\n%s%s\n" % (name, self.calls, "exit 3\n" if name == fail else "",
                                                                    "echo '%s'" % mark if mark else ""))
            os.chmod(p, 0o755)
        return h

    def run_script(self, h, bot_leaves="0", path=None, take="0", how="--release"):
        script = apply.SCRIPT.format(engine=apply.config.shell_path(h), repo="''", name="'box'", instance="'git@x:i.git'",
                                     bot_leaves=bot_leaves, allow=self.allow, take=take, how=how)
        env = dict(os.environ, HOME=self.home, CALLS=self.calls)
        if path:
            env["PATH"] = path + os.pathsep + os.environ["PATH"]
        return subprocess.run(["sh", "-c", script], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              universal_newlines=True, env=env)

    def test_every_step_in_order_and_the_installer_last(self):
        r = self.run_script(self.engine())
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertEqual(self.called()[:3], ["home-pull --quiet --take-refused", "instance join git@x:i.git",
                                             "instance pull --take-refused"])
        self.assertEqual([c.split()[0] for c in self.called()], ["home-pull", "instance", "instance", "install"])
        self.assertIn("ok core.instance", r.stdout)
        with open(os.path.join(self.home, ".config/altention/host")) as f:
            self.assertEqual(f.read(), "box\n")

    def test_the_engine_through_update_py_and_its_check(self):
        """C-57 U5: an engine with altention/update.py takes the newest release through it (its syntax and load check),
        not home-pull --take-refused; it refusing stops the machine there; --take-refused goes past, as before."""
        for code, take, first in ((0, "0", "update --release"), (1, "0", "update --release"),
                                  (0, "1", "home-pull --quiet --take-refused")):
            with self.subTest(code=code, take=take):
                if os.path.exists(self.calls):
                    os.remove(self.calls)
                shutil.rmtree(os.path.join(self.tmp, "engine 引擎"), ignore_errors=True)
                h = self.engine()
                with open(self.mkfile(h, "altention/update.py"), "w") as f:
                    f.write("import sys\nopen(%r, 'a').write('update ' + ' '.join(sys.argv[1:]) + '\\n')\nsys.exit(%d)\n"
                            % (self.calls, code))
                r = self.run_script(h, take=take)
                self.assertEqual(self.called()[0], first, r.stdout)
                if code:
                    self.assertNotEqual(r.returncode, 0)
                    self.assertIn("停在「更新引擎（先檢查新版能不能正常載入）」", r.stdout)
                    self.assertEqual(len(self.called()), 1, "nothing after it")
                else:
                    self.assertEqual(r.returncode, 0, r.stdout)

    def test_the_git_lock_held_is_waited_for_then_said_not_taken_for_done(self):
        """Manager on U5: update.py --release exit 75 (the git lock held, nothing done) is tried again, about 2 minutes
        in all; still held: that machine stops, said, never installed on as if updated."""
        for after, code in ((2, 0), (99, 1)):
            with self.subTest(after=after):
                if os.path.exists(self.calls):
                    os.remove(self.calls)
                shutil.rmtree(os.path.join(self.tmp, "engine 引擎"), ignore_errors=True)
                h = self.engine()
                count = os.path.join(self.tmp, "count")
                if os.path.exists(count):
                    os.remove(count)
                with open(self.mkfile(h, "altention/update.py"), "w") as f:
                    f.write("import os, sys\nopen(%r, 'a').write('update\\n')\nn = len(open(%r).read().splitlines())\n"
                            "sys.exit(75 if n <= %d else 0)\n" % (count, count, after))
                os.environ["ALT_APPLY_RETRY_SECS"] = "0"
                try:
                    r = self.run_script(h)
                finally:
                    del os.environ["ALT_APPLY_RETRY_SECS"]
                with open(count) as f:
                    tries = len(f.read().splitlines())
                if code == 0:
                    self.assertEqual((r.returncode, tries), (0, 3), r.stdout)
                    self.assertEqual(self.called()[-1].split()[0], "install")
                else:
                    self.assertNotEqual(r.returncode, 0)
                    self.assertEqual(tries, 12)
                    self.assertIn("引擎這次沒換版（有別的程序在用 git", r.stdout)
                    self.assertNotIn("install", " ".join(self.called()))

    def test_branch_mode_takes_a_plain_round(self):
        """#code-review of 49b8896: the machines follow their branch (core.update branch): update.py without --release
        (the branch, same check), told to say a held git lock by its exit."""
        h = self.engine()
        with open(self.mkfile(h, "altention/update.py"), "w") as f:
            f.write("import os, sys\nopen(%r, 'a').write('update [' + ' '.join(sys.argv[1:]) + '] ' + "
                    "os.environ.get('ALT_UPDATE_LOCKED_EXIT', '-') + '\\n')\n" % self.calls)
        r = self.run_script(h, how="")
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertEqual(self.called()[0], "update [] 1")

    def mkfile(self, h, rel):
        os.makedirs(os.path.dirname(os.path.join(h, rel)), exist_ok=True)
        return os.path.join(h, rel)

    def test_the_installer_is_told_not_to_check_the_path(self):  # it would run the user's rc files every apply
        self.run_script(self.engine())
        with open(self.calls + ".path") as f:
            self.assertEqual(f.read(), "1\n")

    def test_allow_time_and_hold_are_passed_to_the_installer(self):
        self.allow = "--allow-time chatlog/sync --hold progress"
        self.run_script(self.engine())
        self.assertEqual(self.called()[-1], "install --allow-time chatlog/sync --hold progress")

    def test_a_failing_installer_or_step_stops_and_says_so(self):
        for fail, before in (("home-pull", []), ("instance", ["home-pull"]), ("install", ["home-pull", "instance", "instance"])):
            with self.subTest(fail=fail):
                if os.path.exists(self.calls):
                    os.remove(self.calls)
                shutil.rmtree(os.path.join(self.tmp, "engine 引擎"), ignore_errors=True)
                r = self.run_script(self.engine(fail))
                self.assertNotEqual(r.returncode, 0)
                self.assertEqual([c.split()[0] for c in self.called()], before + [fail])
                self.assertIn("停在「", r.stdout)

    def test_an_installer_that_gives_0_without_its_last_line_stops(self):
        """A stand-in for install.sh (or anything of its name ahead of it) that does nothing and gives 0."""
        r = self.run_script(self.engine(silent=("install",)))
        self.assertNotEqual(r.returncode, 0, r.stdout)
        self.assertIn("沒看到它跑完的那一行（ok core.done）", r.stdout)

    def test_a_stand_in_for_instance_join_stops_there(self):
        r = self.run_script(self.engine(silent=("instance",)))
        self.assertNotEqual(r.returncode, 0, r.stdout)
        self.assertIn("沒看到它跑完的記號（ALT-STEP-OK instance-join）", r.stdout)
        self.assertNotIn("install", " ".join(self.called()))

    def test_an_env_grep_or_sed_that_only_gives_0_changes_nothing(self):
        """A machine's PATH had an installer's ~/.local/bin/env ahead (2026-10-06); a stand-in grep made every mark
        look found (#code-review-2). The script uses neither env nor grep to decide."""
        b = self.fake_bin(["env", "grep", "sed"])
        r = self.run_script(self.engine(silent=("install",)), path=b)
        self.assertNotEqual(r.returncode, 0, r.stdout)
        self.assertIn("沒看到它跑完的那一行", r.stdout)
        self.assertNotRegex(apply.SCRIPT + apply.HELD, r"env [A-Z_]+=")

    def test_a_git_that_gives_no_hash_stops_before_anything(self):
        b = self.fake_bin(["git"])
        r = self.run_script(self.engine(), path=b)
        self.assertNotEqual(r.returncode, 0, r.stdout)
        self.assertIn("讀不到引擎的版本", r.stdout)
        self.assertEqual([c.split()[0] for c in self.called() if not c.startswith("git")], ["home-pull"])

    def test_a_bot_that_moved_away_is_stopped_before_the_installer(self):
        r = self.run_script(self.engine(), bot_leaves="1")
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertEqual([c.split()[0] for c in self.called()][-2:], ["uninstall", "install"])
        os.remove(self.calls)
        shutil.rmtree(os.path.join(self.tmp, "engine 引擎"))
        r = self.run_script(self.engine(fail="uninstall"), bot_leaves="1")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("停在「停掉機器人", r.stdout)
        self.assertNotIn("install", [c.split()[0] for c in self.called()], "its lines stay while it may still run")

    def test_a_stopped_supervisor_is_3_any_other_todo_is_2(self):
        self.stopped = True
        r = self.run_script(self.engine())
        self.assertEqual(r.returncode, 3, r.stdout)
        shutil.rmtree(os.path.join(self.tmp, "engine 引擎"))
        self.todo = True
        r = self.run_script(self.engine())
        self.assertEqual(r.returncode, 2, r.stdout)
        self.assertIn("todo dcbot.token", r.stdout)

    def test_no_engine_and_nowhere_to_clone_from_stops(self):
        script = apply.SCRIPT.format(engine=apply.config.shell_path(os.path.join(self.tmp, "none")), repo="''", name="''",
                                     instance="''", bot_leaves="0", allow="", take="0", how="--release")
        r = subprocess.run(["sh", "-c", script], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           universal_newlines=True, env=dict(os.environ, HOME=self.home))
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("不知道從哪裡 clone", r.stdout)


class Installers(Rig):
    """The plugins' install scripts: never a crontab call, never settings.json; plugins.py apply writes those
    (manager 2026-10-06). The light ones run here for real in a throwaway home; dcbot's and usage's (pip, node) are in
    #code-review-2's rehearsal."""

    def install(self, script, extra_env=None):
        b = self.fake_bin(["crontab", "claude"])
        os.makedirs(os.path.join(self.home, "logs", ".git"))
        env = dict(os.environ, HOME=self.home, CALLS=self.calls, PATH=b + os.pathsep + os.environ["PATH"],
                   CHATLOG_REPO=os.path.join(self.home, "logs"), CHATLOG_REMOTE="none", ALT_INSTANCE="",
                   SHELL="")  # no shell of the user's started (core.path)
        env.update(extra_env or {})
        return subprocess.run([os.path.join(ENGINE, script)] + (["--links"] if script == "install.sh" else []),
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              universal_newlines=True, env=env, stdin=subprocess.DEVNULL)

    def test_core_chatlog_and_pm_touch_neither(self):
        for script, env in (("install.sh", {"CHATLOG": "1"}), ("chatlog/install.sh", None), ("progress/install.sh", None)):
            with self.subTest(script=script):
                shutil.rmtree(self.home)
                os.makedirs(self.home)
                r = self.install(script, env)
                self.assertEqual([c for c in self.called() if c.startswith("crontab")], [], r.stdout)
                self.assertFalse(os.path.exists(os.path.join(self.home, ".claude", "settings.json")), r.stdout)

    def test_every_plugin_install_script_says_it_ran_and_only_on_success(self):
        """ops/install.py looks for these marks (a stand-in that does nothing and gives 0 is a failure)."""
        for script, name in (("chatlog/install.sh", "chatlog"), ("dcbot/install.sh", "dcbot"), ("progress/install.sh", "progress"),
                             ("usage/install.sh", "usage"),
                             ("dcbot/uninstall.sh", "dcbot-uninstall")):
            with open(os.path.join(ENGINE, script), encoding="utf-8") as f:
                self.assertIn('[ "$rc" = 0 ] && echo "ALT-STEP-OK %s"' % name, f.read(), script)
        r = subprocess.run([os.path.join(ENGINE, "progress/install.sh"), "--no-crom"], stdout=subprocess.PIPE,
                           stderr=subprocess.STDOUT, universal_newlines=True, stdin=subprocess.DEVNULL)
        self.assertNotIn("ALT-STEP-OK", r.stdout, "not on a refusal")

    def test_an_unknown_argument_does_nothing(self):
        for script in ("install.sh", "chatlog/install.sh", "dcbot/install.sh", "progress/install.sh", "usage/install.sh",
                       "dcbot/uninstall.sh"):
            with self.subTest(script=script):
                b = self.fake_bin(["crontab"])
                r = subprocess.run([os.path.join(ENGINE, script), "--no-crom"], stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT, universal_newlines=True, stdin=subprocess.DEVNULL,
                                   env=dict(os.environ, HOME=self.home, CALLS=self.calls, PATH=b + os.pathsep + os.environ["PATH"]))
                self.assertEqual(r.returncode, 1 if script == "install.sh" else 2, r.stdout)  # the installer: fail is 1
                self.assertEqual(self.called(), [])


class StopFlag(Rig):
    """dcbot/install.sh removes state/stop only when uninstall.sh wrote it; dcbot/uninstall.sh stops the bot and
    never calls crontab nor dchook (manager 2026-10-06)."""

    def block(self, content):
        with open(os.path.join(ENGINE, "dcbot/install.sh"), encoding="utf-8") as f:
            text = f.read()
        code = text[text.index("# stop-flag: begin"):text.index("# stop-flag: end")]
        d = os.path.join(self.tmp, "dc")
        os.makedirs(os.path.join(d, "state"), exist_ok=True)
        stop = os.path.join(d, "state", "stop")
        if content is not None:
            with open(stop, "w") as f:
                f.write(content)
        r = subprocess.run(["bash", "-c", "set -euo pipefail\n%s\necho STOPPED=$STOPPED" % code],
                           cwd=d, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True)
        self.assertEqual(r.returncode, 0, r.stdout)
        return r.stdout, os.path.exists(stop)

    def test_only_the_uninstall_kind_is_removed_by_apply(self):
        for content, stays in ((None, False), ("uninstalled by apply 2026-10-06T21:00:00+0800\n", False), ("", True),
                               ("rescue: do not start\n", True), ("uninstalled by hand\n", True)):
            with self.subTest(content=content):
                shutil.rmtree(os.path.join(self.tmp, "dc"), ignore_errors=True)
                out, there = self.block(content)
                self.assertEqual(there, stays, out)
                self.assertIn("STOPPED=%d" % (1 if stays else 0), out)
                if stays:
                    self.assertIn("supervisor 被人停著（state/stop），沒有啟動", out)

    def test_an_unreadable_flag_is_someone_elses(self):
        shutil.rmtree(os.path.join(self.tmp, "dc"), ignore_errors=True)
        d = os.path.join(self.tmp, "dc", "state")
        os.makedirs(os.path.join(d, "stop"))  # a folder: no contents to read
        out, there = self.block(None)
        self.assertTrue(there)
        self.assertIn("STOPPED=1", out)

    def test_uninstall_stops_and_touches_no_crontab_or_hook(self):
        with open(os.path.join(ENGINE, "dcbot", "uninstall.sh"), encoding="utf-8") as f:
            text = f.read()
        self.assertNotIn("crontab", text)
        self.assertNotIn("dchook", text)
        self.assertIn("uninstalled by apply", text)


class Machines(unittest.TestCase):
    INV = {"machines": {"a": {"ssh": ["A"], "roles": ["chatlog"]}, "b": {"ssh": ["B"], "roles": ["chatlog", "progress", "dcbot"]},
                        "c": {"ssh": ["C"], "roles": ["chatlog"]}}}

    def run_apply(self, args, places=None, held=None, codes=None, after=None):
        places = places or {"a": ([row("chatlog", "on"), row("progress", "off", "one")], [], []),
                            "b": ([row("chatlog", "on"), row("progress", "on", "one")], [], []),
                            "c": ([row("chatlog", "on"), row("progress", "off", "one")], [], [])}
        held = held or {}
        ran, asked = [], []
        self.asked = asked

        def fake_run_on(m, script, interpreter="python3", timeout=600):
            name = m["ssh"][0].lower()
            if "plugins.py\" held" in script:
                asked.append(name)
                got = held.get(name, {"progress": False, "bot": False})
                if got is None:
                    return None, 255, "", "No route to host"
                return m["ssh"][0], 0, json.dumps(got) + "\n", ""
            ran.append((name, "preview" if "沒有更新" in script else ("apply+stop-bot" if "\nif [ 1 = 1 ]" in script else "apply")))
            if name in (after or {}):
                held[name] = after[name]  # what its crontab holds once plugins.py apply has run there
            if "--allow-time" in script:
                line = script[script.index("--allow-time"):].split("\n")[0]  # each word quoted (config.shell_quote)
                ran.append((name, " ".join("--allow-time " + x for x in re.findall(r"--allow-time '([^']*)'", line))))
            if "--hold " in script:
                line = script[script.index("--hold "):].split("\n")[0]
                ran.append((name, "hold " + " ".join(sorted(re.findall(r"--hold '([^']*)'", line)))))
            return m["ssh"][0], (codes or {}).get(name, 0), "ok", ""
        out = io.StringIO()
        with mock.patch.object(apply, "inventory", return_value=json.loads(json.dumps(self.INV))), \
                mock.patch.object(apply.plugins, "placements", side_effect=lambda n: places[n]), \
                mock.patch.object(apply.config, "bot_machine", return_value=("b", None, False)), \
                mock.patch.object(apply, "run_on", fake_run_on), \
                mock.patch.object(apply, "engine_origin", return_value="git@x:e.git"), \
                contextlib.redirect_stdout(out):
            code = apply.main(args)
        return code, out.getvalue(), ran

    def test_the_engine_step_follows_the_instances_core_update(self):
        """#code-review of 49b8896 (manager): core.update branch → update.py's plain round (the branch); notify or
        release → --release (the newest release)."""
        scripts = []

        def keep(m, script, **k):
            scripts.append(script)
            if "plugins.py\" held" in script:
                return m["ssh"][0], 0, json.dumps({"bot": False}) + "\n", ""
            return m["ssh"][0], 0, "ok", ""
        for mode, how in (("branch", 'update.py"  && RC=0'), ("notify", 'update.py" --release && RC=0'),
                          ("release", 'update.py" --release && RC=0')):
            with self.subTest(mode=mode):
                del scripts[:]
                with mock.patch.object(apply.config, "setting", lambda key, default=None: mode if key == "core.update" else default), \
                        mock.patch.object(apply, "inventory", return_value={"machines": {"a": {"ssh": ["A"], "roles": []}}}), \
                        mock.patch.object(apply.plugins, "placements", side_effect=lambda n: ([row("chatlog", "on")], [], [])), \
                        mock.patch.object(apply.config, "bot_machine", return_value=("b", None, False)), \
                        mock.patch.object(apply, "run_on", keep), \
                        mock.patch.object(apply, "engine_origin", return_value="git@x:e.git"), \
                        contextlib.redirect_stdout(io.StringIO()):
                    apply.main(["a"])
                applied = [x for x in scripts if "release_now" in x]
                self.assertTrue(applied, scripts)
                self.assertIn(how, applied[-1])

    def test_machines_without_a_single_plugin_go_first(self):
        code, text, ran = self.run_apply(["b", "a", "c"])
        self.assertEqual(code, 0, text)
        self.assertEqual(ran, [("a", "apply"), ("c", "apply"), ("b", "apply")])

    def test_a_move_is_done_in_one_run_the_loser_first(self):
        """progress from b to a: b (which keeps the bot) loses progress and goes first; a then finds b clean and gets it."""
        places = {"a": ([row("chatlog", "on"), row("progress", "on", "one")], [], []),
                  "b": ([row("chatlog", "on"), row("progress", "off", "one")], [], []),
                  "c": ([row("chatlog", "on"), row("progress", "off", "one")], [], [])}
        held = {"a": {"progress": False, "bot": False}, "b": {"progress": "marked", "bot": "marked"}, "c": {"progress": False, "bot": False}}
        code, text, ran = self.run_apply(["all"], places=places, held=held, after={"b": {"progress": False, "bot": "marked"}})
        self.assertEqual(code, 0, text)
        self.assertEqual(ran, [("b", "apply"), ("c", "apply"), ("a", "apply")])

    def test_old_format_lines_elsewhere_say_apply_will_not_take_them(self):
        code, text, ran = self.run_apply(["b"], held={"b": {"progress": False, "bot": "marked"}, "a": {"progress": "old", "bot": False}})
        self.assertEqual((code, ran), (1, [("b", "apply"), ("b", "hold progress")]))
        self.assertIn("不是新標記，plugins.py apply 不會拿", text)

    def test_allow_time_goes_to_every_machine_and_a_bare_one_is_refused(self):
        code, text, ran = self.run_apply(["a", "--allow-time", "chatlog/sync"])
        self.assertIn(("a", "--allow-time chatlog/sync"), ran)
        code, text, ran = self.run_apply(["a", "--allow-time"])
        self.assertEqual((code, ran), (2, []))

    def test_a_swap_of_two_machines_plugins_comes_right_in_two_runs(self):
        """progress b→a and usage a→b at once: each is blocked by the other the first time, but lets go of its own."""
        places = {"a": ([row("progress", "on", "one"), row("usage", "off", "one")], [], []),
                  "b": ([row("progress", "off", "one"), row("usage", "on", "one")], [], []),
                  "c": ([row("progress", "off", "one"), row("usage", "off", "one")], [], [])}
        held = {"a": {"progress": False, "usage": "marked", "bot": False}, "b": {"progress": "marked", "usage": False, "bot": "marked"},
                "c": {"progress": False, "usage": False, "bot": False}}
        after = {"a": {"progress": False, "usage": False, "bot": False}, "b": {"progress": False, "usage": False, "bot": "marked"}}
        code, text, ran = self.run_apply(["a", "b"], places=places, held=held, after=after)
        self.assertEqual(code, 1)
        self.assertIn(("a", "hold progress"), ran, "a waits for progress, but lets usage go")
        self.assertNotIn("hold", " ".join(r[1] for r in ran if r[0] == "b"), "b finds usage gone from a already")
        code, text, ran = self.run_apply(["a", "b"], places=places, held=held)
        self.assertEqual(code, 0, text)
        self.assertEqual([r for r in ran if r[1].startswith("hold")], [])

    def test_a_supervisor_left_stopped_is_exit_3_a_failure_elsewhere_wins(self):
        code, text, ran = self.run_apply(["all"], codes={"b": 3})
        self.assertEqual(code, 3, text)
        self.assertIn("只有 supervisor 被人停著", text)
        self.assertNotIn("✗ 失敗", text)
        code, text, ran = self.run_apply(["all"], codes={"b": 3, "a": 1})
        self.assertEqual(code, 1)

    def test_not_knowing_where_the_bot_runs_touches_no_machine(self):
        for got in ((None, None, False), RuntimeError("清單壞了")):
            with self.subTest(got=str(got)):
                patch = {"side_effect": got} if isinstance(got, Exception) else {"return_value": got}
                with mock.patch.object(apply.config, "bot_machine", **patch):
                    out = io.StringIO()
                    with mock.patch.object(apply, "inventory", return_value=json.loads(json.dumps(self.INV))), \
                            mock.patch.object(apply, "run_on") as run, contextlib.redirect_stdout(out):
                        code = apply.main(["all"])
                self.assertEqual(code, 1)
                self.assertIn("不知道機器人在哪台", out.getvalue())
                run.assert_not_called()

    def test_one_held_here_already_is_not_a_move_nobody_else_is_asked(self):
        code, text, ran = self.run_apply(["b"], held={"b": {"progress": "marked", "bot": "marked"}, "c": None})
        self.assertEqual((code, ran), (0, [("b", "apply")]), text)
        self.assertEqual(self.asked, ["b"])

    def test_the_machine_itself_unreadable_is_not_done(self):
        code, text, ran = self.run_apply(["b"], held={"b": {"unknown": "crontab -l 逾時"}})
        self.assertEqual((code, ran), (1, []))
        self.assertIn("讀不出它自己有哪些排程", text)

    def test_a_machine_the_bot_left_has_it_stopped_first(self):
        code, text, ran = self.run_apply(["a"], held={"a": {"progress": False, "bot": "marked"}})
        self.assertEqual((code, ran), (0, [("a", "apply+stop-bot")]), text)
        self.assertIn("先停掉它", text)

    def test_a_single_plugin_still_on_another_machine_is_not_added(self):
        code, text, ran = self.run_apply(["b"], held={"a": {"progress": "marked", "bot": False}})
        self.assertEqual(code, 1)
        self.assertEqual(ran, [("b", "apply"), ("b", "hold progress")], "the rest done, progress held back")
        self.assertIn("progress 在 a：還有它們的排程（先在那台跑 apply 拿掉）", text)
        self.assertIn("--force-move", text)

    def test_a_machine_that_cannot_be_reached_counts_as_still_holding_it(self):
        code, text, ran = self.run_apply(["b"], held={"c": None})
        self.assertEqual((code, ran), (1, [("b", "apply"), ("b", "hold bot progress")]))
        self.assertIn("progress、bot 在 c：連不上", text)
        code, text, ran = self.run_apply(["b"], held={"c": {"unknown": "crontab -l 逾時"}})
        self.assertEqual((code, ran), (1, [("b", "apply"), ("b", "hold bot progress")]))
        self.assertIn("讀不出來（crontab -l 逾時）", text)

    def test_force_move_does_it_and_says_the_other_may_still_run_it(self):
        code, text, ran = self.run_apply(["b", "--force-move"], held={"a": {"progress": "marked", "bot": False}})
        self.assertEqual((code, ran), (0, [("b", "apply")]))
        self.assertIn("可能還在跑", text)

    def test_an_unknown_placement_leaves_that_machine_alone_only(self):
        places = {"b": ([row("chatlog", "on"), row("progress", "on", "one")], [], []),
                  "c": ([row("chatlog", "unknown")], ["實例設定讀不出來"], ["chatlog：不知道（實例設定讀不出來）"]),
                  "a": ([row("chatlog", "on")], [], [])}
        code, text, ran = self.run_apply(["all"], places=places)
        self.assertEqual(code, 1)
        self.assertEqual(sorted(ran), [("a", "apply"), ("b", "apply")])
        self.assertIn("算不出來", text)

    def test_two_machines_for_one_single_plugin_touches_nothing(self):
        places = {n: ([row("progress", "on", "one")], [], []) for n in "abc"}
        code, text, ran = self.run_apply(["a"], places=places)
        self.assertEqual((code, ran), (1, []))
        self.assertIn("兩台以上", text)

    def test_dry_run_shows_what_is_held_back(self):
        code, text, ran = self.run_apply(["b", "--dry-run"], held={"a": {"progress": "marked", "bot": False}})
        self.assertIn(("b", "hold progress"), ran)

    def test_dry_run_previews_and_unknown_flags_do_nothing(self):
        code, text, ran = self.run_apply(["all", "--dry-run"])
        self.assertEqual(ran, [("a", "preview"), ("c", "preview"), ("b", "preview")])
        self.assertNotIn("doctor", text)
        code, text, ran = self.run_apply(["all", "--dryrun"])
        self.assertEqual((code, ran), (2, []))

    def test_a_failed_machine_does_not_stop_the_others(self):
        code, text, ran = self.run_apply(["all"], codes={"a": 1})
        self.assertEqual(code, 1)
        self.assertEqual([n for n, _ in ran], ["a", "c", "b"])


class Held(unittest.TestCase):
    def held(self, lines, names, engine="/srv/e"):
        out = io.StringIO()
        raw = b"".join(l.encode("utf-8") + b"\n" for l in lines)
        with mock.patch.object(plancore, "read_crontab_raw", return_value=raw), \
                mock.patch.object(plugins.plugin, "ENGINE", engine):
            code = plugins.held(names, out)
        return code, json.loads(out.getvalue())

    def test_markers_new_and_old_and_files_in_the_folder(self):
        lines = ['0 8 * * * dcpost 1 "x" >>"~/o" 2>&1  # altention:planner',
                 '7 * * * * /usr/bin/python3 "/srv/e/usage/collect.py" # usage',
                 '*/5 * * * * cd "/srv/e/dcbot" && /usr/bin/python3 supervisor.py >> state/supervisor.out 2>&1',
                 '# */10 * * * * /usr/bin/python3 "/srv/e/progress/progress.py" run  # altention:progress',
                 '0 1 * * * /opt/acct/report.sh --month   # usage report']
        code, got = self.held(lines, ["planner", "usage", "bot", "progress", "council"])
        self.assertEqual(code, 0)
        self.assertEqual(got, {"planner": "marked", "usage": "old", "bot": "old", "progress": False, "council": False})

    def test_marked_only_when_every_line_carries_the_new_marker(self):
        code, got = self.held(['*/5 * * * * cd "/srv/e/dcbot" && /usr/bin/python3 supervisor.py  # altention:core',
                               '7 * * * * /usr/bin/python3 "/srv/e/usage/collect.py"  # altention:usage',
                               '9 * * * * /usr/bin/python3 "/srv/e/usage/collect.py" # usage'], ["bot", "usage"])
        self.assertEqual(got, {"bot": "marked", "usage": "old"})

    def test_someone_elses_usage_line_alone_is_not_ours(self):
        code, got = self.held(["0 1 * * * /opt/acct/report.sh --month   # usage report"], ["usage"])
        self.assertEqual(got, {"usage": False})

    def test_an_unreadable_crontab_is_unknown(self):
        out = io.StringIO()
        with mock.patch.object(plancore, "read_crontab_raw", side_effect=plancore.Unknown("crontab -l 逾時")):
            code = plugins.held(["progress"], out)
        self.assertEqual(code, 2)
        self.assertIn("unknown", json.loads(out.getvalue()))


if __name__ == "__main__":
    unittest.main()
