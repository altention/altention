"""bin/home-pull against throwaway repos: no autostash markers, no lost edits, recovery from a killed rebase."""
import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))))
from altention import testing as _testing  # noqa: E402
_testing.guard_home()  # tests never touch the real home (altention/testing.py)
import os
import shutil
import subprocess
import tempfile
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def sh(cwd, *args):
    return subprocess.run(list(args), cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)


def branch_mode(home, mode="branch"):
    """The mode last read (update-mode, altention/update.py): what home-pull takes when no instance is seen."""
    folder = os.path.join(home, ".local", "state", "altention")
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, "update-mode"), "w") as f:
        f.write('{"mode": "%s", "at": "2026-10-14T00:00:00+0000"}' % mode)


class Rig(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        branch_mode(os.path.expanduser("~"))  # these test what follows the branch: the mode our machines keep
        env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com", "GIT_COMMITTER_NAME": "t",
               "GIT_COMMITTER_EMAIL": "t@example.com"}
        os.environ.update(env)
        self.origin = os.path.join(self.tmp, "origin.git")
        sh(self.tmp, "git", "init", "-q", "--bare", self.origin)  # git 2.25 (Ubuntu 20.04) has no `init -b`
        sh(self.origin, "git", "symbolic-ref", "HEAD", "refs/heads/main")
        self.a, self.b = os.path.join(self.tmp, "a"), os.path.join(self.tmp, "b")
        sh(self.tmp, "git", "clone", "-q", self.origin, self.a)
        sh(self.a, "git", "checkout", "-q", "-b", "main")
        os.makedirs(os.path.join(self.a, "bin"))
        shutil.copy(os.path.join(HERE, "home-pull"), os.path.join(self.a, "bin", "home-pull"))
        shutil.copy(os.path.join(HERE, "home-git"), os.path.join(self.a, "bin", "home-git"))
        shutil.copytree(os.path.join(os.path.dirname(HERE), "altention"), os.path.join(self.a, "altention"),
                        ignore=shutil.ignore_patterns("__pycache__"))
        self.write(self.a, ".gitignore", "__pycache__/\n")  # as in the engine: running it must not dirty the tree
        self.write(self.a, "notes.md", "one\n")
        self.write(self.a, "other.md", "x\n")
        sh(self.a, "git", "add", "-A")
        sh(self.a, "git", "commit", "-q", "-m", "init")
        sh(self.a, "git", "push", "-q", "-u", "origin", "main")
        sh(self.tmp, "git", "clone", "-q", "-b", "main", self.origin, self.b)

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def write(self, repo, name, text, mode="w"):
        with open(os.path.join(repo, name), mode) as f:
            f.write(text)

    def read(self, repo, name):
        with open(os.path.join(repo, name)) as f:
            return f.read()

    def upstream(self, name="notes.md", text="from b\n"):
        self.write(self.b, name, text, "a")
        sh(self.b, "git", "commit", "-qam", "b")
        sh(self.b, "git", "push", "-q")

    def pull(self):
        return sh(self.a, "python3", "bin/home-pull", "--quiet")


class HomePull(Rig):
    def test_behind_with_unrelated_edit_fast_forwards_and_keeps_it(self):
        self.upstream()
        self.write(self.a, "other.md", "mine\n", "a")
        r = self.pull()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("from b", self.read(self.a, "notes.md"))
        self.assertIn("mine", self.read(self.a, "other.md"))

    def test_same_file_edited_here_and_upstream_is_refused_without_markers(self):
        self.upstream()
        self.write(self.a, "notes.md", "mine\n", "a")
        r = self.pull()
        self.assertEqual(r.returncode, 1)
        self.assertNotIn("<<<<<<<", self.read(self.a, "notes.md"))
        self.assertIn("mine", self.read(self.a, "notes.md"), "the edit is still there")
        self.assertEqual(sh(self.a, "git", "stash", "list").stdout, "", "no stash left behind")

    def test_ahead_and_behind_rebases_when_clean_and_refuses_when_dirty(self):
        self.upstream()
        self.write(self.a, "other.md", "local commit\n", "a")
        sh(self.a, "git", "commit", "-qam", "a")
        self.write(self.a, "notes.md", "dirty\n", "a")
        self.assertEqual(self.pull().returncode, 1)
        sh(self.a, "git", "checkout", "--", "notes.md")
        r = self.pull()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("from b", self.read(self.a, "notes.md"))
        self.assertIn("local commit", self.read(self.a, "other.md"))

    def test_a_rebase_killed_halfway_is_cleared_and_its_edits_kept(self):
        self.write(self.a, "other.md", "saved edit\n", "a")
        stash = sh(self.a, "git", "stash", "create").stdout.strip()
        sh(self.a, "git", "checkout", "--", "other.md")
        left = os.path.join(self.a, ".git", "rebase-merge")
        os.makedirs(left)
        self.write(left, "autostash", stash + "\n")  # what a kill -9 right after autostash leaves (no head-name)
        self.upstream()
        r = self.pull()
        self.assertEqual(r.returncode, 1, "fresh and without our mark: a person's rebase, left alone")
        self.assertTrue(os.path.isdir(left))
        self.write(os.path.join(self.a, ".git"), "agent-rebase", "")  # ours (written by sync before it rebases)
        r = self.pull()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(os.path.isdir(left))
        self.assertIn("interrupted rebase", sh(self.a, "git", "stash", "list").stdout)
        self.assertIn("from b", self.read(self.a, "notes.md"))

    def test_detached_head_is_an_error_not_a_silent_success(self):
        sh(self.a, "git", "checkout", "-q", "--detach")
        r = self.pull()
        self.assertEqual(r.returncode, 1)
        self.assertIn("detached", r.stderr)

    def test_an_old_killed_rebase_without_our_mark_is_recovered(self):
        left = os.path.join(self.a, ".git", "rebase-merge")
        os.makedirs(left)
        self.write(left, "autostash", sh(self.a, "git", "rev-parse", "HEAD").stdout)
        old = __import__("time").time() - 4000
        os.utime(left, (old, old))
        self.assertEqual(self.pull().returncode, 0)
        self.assertFalse(os.path.isdir(left))

    def test_stale_git_locks_are_cleared_fresh_ones_wait(self):
        import time as _time
        gd = os.path.join(self.a, ".git")
        self.write(gd, "REBASE_HEAD.lock", "")
        old = _time.time() - 4000
        os.utime(os.path.join(gd, "REBASE_HEAD.lock"), (old, old))
        self.upstream()
        self.assertEqual(self.pull().returncode, 0)
        self.assertFalse(os.path.exists(os.path.join(gd, "REBASE_HEAD.lock")))
        os.makedirs(os.path.join(gd, "rebase-merge"))
        self.write(gd, "agent-rebase", "")
        self.write(gd, "index.lock", "")
        r = self.pull()
        self.assertEqual(r.returncode, 1, "git may still be at work: try again later")
        self.assertTrue(os.path.isdir(os.path.join(gd, "rebase-merge")), "not moved while index.lock is fresh")

    def test_home_git_runs_git_under_the_lock(self):
        self.write(self.a, "docs.md", "x\n")
        r = sh(self.a, "python3", "bin/home-git", "add", "docs.md")
        self.assertEqual(r.returncode, 0, r.stderr)
        r = sh(self.a, "python3", "bin/home-git", "commit", "-q", "-m", "by hand")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("by hand", sh(self.a, "git", "log", "--oneline", "-1").stdout)
        self.assertNotEqual(sh(self.a, "python3", "bin/home-git", "no-such-command").returncode, 0, "git's status is passed on")

    def test_no_remote_is_fine(self):
        sh(self.a, "git", "remote", "remove", "origin")
        r = self.pull()
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_branch_without_upstream_is_an_error(self):
        sh(self.a, "git", "branch", "--unset-upstream")
        r = self.pull()
        self.assertEqual(r.returncode, 1)
        self.assertIn("追蹤", r.stderr)

    def test_time_limit_without_timeout_command_uses_perl(self):
        import importlib.machinery
        import importlib.util
        import sys as _sys
        _sys.path.insert(0, self.a)
        from altention import sync as mod
        from unittest import mock
        with mock.patch.object(mod.shutil, "which", side_effect=lambda n: None if n == "timeout" else "/usr/bin/" + n):
            cmd = mod.limited(["sleep", "5"], 1)
        self.assertEqual(cmd[0], "perl")
        self.assertNotEqual(subprocess.run(cmd).returncode, 0, "killed by the alarm")
        pidfile = os.path.join(self.tmp, "child.pid")
        with mock.patch.object(mod.shutil, "which", side_effect=lambda n: None if n == "timeout" else "/usr/bin/" + n):
            cmd = mod.limited(["sh", "-c", "sleep 30 & echo $! > %s; wait" % pidfile], 1)
        import time as _time
        t0 = _time.time()
        subprocess.run(cmd)
        self.assertLess(_time.time() - t0, 6)
        with open(pidfile) as f:
            child = int(f.read())
        _time.sleep(0.3)
        try:
            os.kill(child, 0)
            alive = True
        except OSError:
            alive = False
        self.assertFalse(alive, "a child of the limited command (git's ssh) ends with it")


if __name__ == "__main__":
    unittest.main()


class Modes(Rig):
    """The instance's core.update (manager 2026-10-14): notify takes nothing unless a person says --take-refused (then
    the newest release); release takes the newest vX.Y.Z tag, never the branch."""
    def setUp(self):
        super().setUp()
        self.home = os.path.join(self.tmp, "home")
        os.makedirs(os.path.join(self.home, ".local", "state", "altention"))
        self.inst = os.path.join(self.tmp, "實例 instance")
        os.makedirs(self.inst)

    def mode(self, value):
        self.write(self.inst, "plugins.json", '{"format": 1, "core": {"update": "%s"}}' % value)

    def run_(self, *args):
        env = dict(os.environ, HOME=self.home, ALT_INSTANCE=self.inst)
        return subprocess.run(["python3", "bin/home-pull"] + list(args), cwd=self.a, env=env, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, universal_newlines=True)

    def head(self, repo):
        return sh(repo, "git", "rev-parse", "HEAD").stdout.strip()

    def tag(self, name):
        sh(self.b, "git", "tag", name)
        sh(self.b, "git", "push", "-q", "origin", name)

    def test_notify_takes_nothing_by_itself(self):
        self.mode("notify")
        before = self.head(self.a)
        self.upstream()
        self.tag("v0.1.0")
        r = self.run_("--quiet")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.head(self.a), before)
        r = self.run_()
        self.assertIn("notify", r.stdout)
        self.assertEqual(self.head(self.a), before)

    def test_notify_on_a_persons_command_takes_the_newest_release(self):
        self.mode("notify")
        self.upstream()
        released = self.head(self.b)
        self.tag("v0.1.0")
        self.upstream(text="after the release\n")
        r = self.run_("--quiet", "--take-refused")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.head(self.a), released, "the release, not the branch")

    def test_release_takes_the_newest_tag_never_the_branch(self):
        self.mode("release")
        before = self.head(self.a)
        self.upstream()
        r = self.run_("--quiet")
        self.assertEqual((r.returncode, self.head(self.a)), (0, before), "no release yet: nothing")
        self.tag("v0.9.0")
        self.upstream(text="second\n")
        released = self.head(self.b)
        self.tag("v0.10.0")
        self.upstream(text="not released\n")
        r = self.run_("--quiet")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.head(self.a), released)

    def test_release_keeps_a_refused_release_out(self):
        self.mode("release")
        before = self.head(self.a)
        self.upstream()
        bad = self.head(self.b)
        self.tag("v0.1.0")
        with open(os.path.join(self.home, ".local", "state", "altention", "last_alive-update"), "w") as f:
            f.write('{"at": 1, "blocked": {"hash": "%s", "why": "語法錯誤", "since": 1}}' % bad)
        r = self.run_("--quiet")
        self.assertEqual(r.returncode, 1)
        self.assertIn("被擋", r.stderr + r.stdout)
        self.assertEqual(self.head(self.a), before)

    def test_branch_takes_the_branch_as_always(self):
        self.mode("branch")
        self.upstream()
        self.tag("v0.1.0")
        self.upstream(text="not released\n")
        r = self.run_("--quiet")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.head(self.a), self.head(self.b))


class Refused(Rig):
    """An upstream altention/update.py refused is not pulled by a plain home-pull: the bot's stream line, the usage
    collector and each channel's Claude call it on their own (code-review, manager 2026-10-07). A person's
    ops/apply.py passes --take-refused. The callers go on with their own work when it says no."""
    def setUp(self):
        super().setUp()
        self.home = os.path.join(self.tmp, "home")
        os.makedirs(os.path.join(self.home, ".local", "state", "altention"))
        branch_mode(self.home)
        os.makedirs(os.path.join(self.home, "altention"))
        os.symlink(self.a, os.path.join(self.home, "altention", "engine"))

    def block(self, h, why="語法錯誤：altention/x.py"):
        import json
        with open(os.path.join(self.home, ".local", "state", "altention", "last_alive-update"), "w") as f:
            json.dump({"at": 1, "blocked": {"hash": h, "why": why, "since": 1}}, f)

    def run_(self, *args, cmd=None):
        env = dict(os.environ, HOME=self.home)
        argv = ["sh", "-c", cmd] if cmd else ["python3", "bin/home-pull", "--quiet"] + list(args)
        return subprocess.run(argv, cwd=self.a, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              universal_newlines=True)

    def head(self, repo):
        return sh(repo, "git", "rev-parse", "HEAD").stdout.strip()

    def test_a_refused_upstream_stays_out_until_forced_or_moved_on(self):
        before = self.head(self.a)
        self.upstream()
        bad = self.head(self.b)
        self.block(bad)
        r = self.run_()
        self.assertEqual(r.returncode, 1)
        self.assertIn("被擋", r.stderr + r.stdout)
        self.assertIn("--take-refused", r.stderr + r.stdout)
        self.assertIn("語法錯誤", r.stderr + r.stdout)
        self.assertEqual(self.head(self.a), before, "not pulled")
        self.assertEqual(self.run_("--take-refused").returncode, 0)
        self.assertEqual(self.head(self.a), bad, "a person's own command takes it")
        sh(self.a, "git", "reset", "-q", "--hard", before)
        self.upstream(text="the fix\n")  # a fix put on top of it: B is still in it
        r = self.run_()
        self.assertEqual(r.returncode, 1, "stacked on the refused one: update.py's to judge")
        self.assertIn("疊在被擋的", r.stderr + r.stdout)
        self.assertIn("等 update 那行判斷", r.stderr + r.stdout)
        self.assertNotIn("--take-refused", r.stderr + r.stdout, "no flag offered for a fix waiting its turn")
        self.assertEqual(self.head(self.a), before)
        self.block("")  # update.py checked the new upstream and let it through (no record any more)
        self.assertEqual(self.run_().returncode, 0)
        self.assertEqual(self.head(self.a), self.head(self.b), "then pulled as usual")
        sh(self.a, "git", "reset", "-q", "--hard", bad)
        self.block(bad)  # a person took B; until update's next round the record still names it
        self.assertEqual(self.run_().returncode, 0, "already in HEAD: nothing to hold back")

    def test_the_stream_line_and_the_usage_collector_go_on_when_it_is_refused(self):
        before = self.head(self.a)
        self.upstream()
        self.block(self.head(self.b))
        for rel, text in (("dcbot/tools/dchook", "print('HOOKS')\n"), ("dcbot/tools/dcremote", "print('STREAMING')\n"),
                          ("usage/remote.py", "print('{\"usage\": 1}')\n")):
            os.makedirs(os.path.dirname(os.path.join(self.a, rel)), exist_ok=True)
            self.write(self.a, rel, text)
        import importlib.util
        from unittest import mock
        sys_path = list(__import__("sys").path)
        try:
            __import__("sys").path.insert(0, os.path.join(os.path.dirname(HERE), "dcbot"))
            from bot import remote
            with mock.patch.object(remote, "engine_of", return_value="~/altention/engine"):
                stream = remote.stream_command("far").replace("--update >/dev/null 2>&1", "--update")
        finally:
            __import__("sys").path[:] = sys_path
        r = self.run_(cmd=stream)
        self.assertIn("HOOKS", r.stdout)
        self.assertIn("STREAMING", r.stdout, "the stream starts all the same")
        self.assertEqual(self.head(self.a), before, "with the engine left as it was")
        spec = importlib.util.spec_from_file_location("collect", os.path.join(os.path.dirname(HERE), "usage", "collect.py"))
        collect = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(collect)
        seen = []

        real = subprocess.run

        def run(argv, **kw):  # this machine is read by the collecting engine's own remote.py, with no pull at all
            seen.append(argv)  # (#architecture-review on dfa579c): the stand-in answers for it
            return real([__import__("sys").executable, "-c", "print('{\"usage\": 1}')"], cwd=self.a, env=dict(os.environ, HOME=self.home),
                        stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
        with mock.patch.object(collect.subprocess, "run", run):
            got = collect.machine_usage("box", {"ssh": ["local"]})
        self.assertEqual(got, {"usage": 1, "machine": "box"}, "usage collected all the same")
        self.assertEqual(self.head(self.a), before)
        self.assertEqual(seen[0][1:], [os.path.join(os.path.dirname(HERE), "usage", "remote.py"), "31"], "no home-pull here")
        self.assertNotIn("--take-refused", " ".join(seen[0]) + stream, "no automatic caller takes a refused hash")

    def test_fetch_failing_is_not_taken_for_a_clear_way(self):
        self.upstream()
        self.block(self.head(self.b))
        before = self.head(self.a)
        sh(self.a, "git", "remote", "set-url", "origin", os.path.join(self.tmp, "gone.git"))
        r = self.run_()
        self.assertEqual(r.returncode, 1)
        self.assertIn("不知道上游是不是被擋的", r.stderr + r.stdout)
        self.assertEqual(self.head(self.a), before)

    def test_deploy_stops_rather_than_rebase_onto_a_refused_upstream(self):
        """deploy.sh: when its push is refused it puts the commit on top of the upstream with sync.update(); before
        that it asks sync.held_back(), and a refused upstream (or a check that fails) stops the deploy: not pushed,
        no restart (manager 2026-10-07)."""
        with open(os.path.join(os.path.dirname(HERE), "dcbot", "deploy.sh")) as f:
            text = f.read()
        push = text.index("if timeout 300 git push -q")
        held, rebase = text.index("sync.held_back(", push), text.index("sync.update(r)", push)
        self.assertLess(held, rebase, "asked before rebasing")
        between = text[held:rebase]
        self.assertIn("exit 1", between)
        self.assertIn("檢查本身出錯", between, "a check that fails stops too")
        restart = text.index("restart-requested")
        self.assertLess(rebase, restart)
        # the snippet itself, run here against a refused upstream: it says why
        self.upstream()
        self.block(self.head(self.b))
        snippet = text[text.index("/usr/bin/python3 -c '", held - 200) + len("/usr/bin/python3 -c '"):]
        snippet = snippet[:snippet.index("')\"")]
        shutil.copytree(os.path.join(os.path.dirname(HERE), "altention"), os.path.join(self.a, "altention"),
                        dirs_exist_ok=True, ignore=shutil.ignore_patterns("__pycache__"))
        r = subprocess.run(["python3", "-c", snippet], cwd=self.a, env=dict(os.environ, HOME=self.home),
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
        self.assertIn("被擋", r.stdout)
        self.assertIn("沒有部署", r.stdout)

    def test_an_unreadable_record_is_pulled_as_before_and_written_down(self):
        self.upstream()
        with open(os.path.join(self.home, ".local", "state", "altention", "last_alive-update"), "w") as f:
            f.write("{ cut short")
        self.assertEqual(self.run_().returncode, 0)
        self.assertEqual(self.head(self.a), self.head(self.b), "no record: pulled as before")
        with open(os.path.join(self.home, ".local", "state", "altention", "alerts")) as f:
            self.assertIn("home-pull", f.read(), "and said where someone will see it")
