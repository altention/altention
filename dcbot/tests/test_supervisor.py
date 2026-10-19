"""supervisor.py, on a throwaway engine repo with a fake bot: a rollback puts the whole tree back to last_good (its
tree hash exactly), or only dcbot/ and altention/ when a move into the instance lies in between; it saves other
channels' uncommitted edits first; it takes the shared git lock (or goes ahead after waiting); the sync it runs in a
child process may exit, hang or raise and the supervisor goes on; git hanging does not keep the bot down; last_good
follows HEAD; a trial fails on two real failed events, not on one, nor on network ones."""
import fcntl
import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(HERE))
from altention import testing  # noqa: E402
from tests import tmuxguard  # noqa: F401,E402  (no test reaches a real tmux server)
testing.guard_home()

ENGINE_REAL = os.path.dirname(HERE)
if os.environ.get("DCBOT_SUPERVISOR_TESTS") == "skip":  # deploy.sh runs these on their own, only when supervisor.py changes
    raise unittest.SkipTest("supervisor tests run separately (deploy.sh)")

FAKE_BOT = '''import os, sys, time, subprocess
sys.path.insert(0, os.path.dirname(os.getcwd()))
import altention.thing  # a broken altention/ breaks the bot here
tree = subprocess.run(["git", "rev-parse", "HEAD:./"], stdout=subprocess.PIPE, universal_newlines=True).stdout.strip()
bad = os.environ.get("FAKE_BAD")  # names of events this version sets aside as failed
if bad:
    os.makedirs(os.path.join("state", "events-bad"), exist_ok=True)
    for name in bad.split(","):
        open(os.path.join("state", "events-bad", name), "w").close()
open(os.path.join("state", "ready"), "w").write("%s %d\\n" % (tree, time.time()))
while not os.path.exists(os.path.join("state", "stop")):
    if os.path.exists(os.path.join("state", "restart-requested")):
        os.remove(os.path.join("state", "restart-requested"))
        sys.exit(42)
    time.sleep(0.2)
'''


def sh(cwd, *args):
    return subprocess.run(list(args), cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)


def slurp(path):
    with open(path) as f:
        return f.read()


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(text)


class Rig(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sup 中文 ")
        os.environ.update({"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com", "GIT_COMMITTER_NAME": "t",
                           "GIT_COMMITTER_EMAIL": "t@example.com"})
        self.saved_env = {k: os.environ.get(k) for k in ("GIT_SSH_COMMAND", "FAKE_BAD", "ALT_INSTANCE")}
        self.mode("branch")  # what our machines keep; the release and notify cases set their own
        self.origin = os.path.join(self.tmp, "origin.git")
        sh(self.tmp, "git", "init", "-q", "--bare", self.origin)
        sh(self.origin, "git", "symbolic-ref", "HEAD", "refs/heads/main")
        self.engine = os.path.join(self.tmp, "engine")
        dc = os.path.join(self.engine, "dcbot")
        os.makedirs(os.path.join(dc, "bot"))
        shutil.copy(os.path.join(HERE, "supervisor.py"), os.path.join(dc, "supervisor.py"))
        write(os.path.join(dc, "bot", "__init__.py"), "")
        write(os.path.join(dc, "bot", "main.py"), FAKE_BOT)
        write(os.path.join(dc, ".gitignore"), "state/\n.venv/\n__pycache__/\n")
        os.makedirs(os.path.join(dc, ".venv", "bin"))
        os.symlink(sys.executable, os.path.join(dc, ".venv", "bin", "python"))
        shutil.copytree(os.path.join(ENGINE_REAL, "altention"), os.path.join(self.engine, "altention"),
                        ignore=shutil.ignore_patterns("tests", "__pycache__"))
        write(os.path.join(self.engine, "altention", "thing.py"), "OK = 1\n")
        write(os.path.join(self.engine, "other.txt"), "untouched\n")
        write(os.path.join(self.engine, "ops", "x.py"), "X = 0\n")
        write(os.path.join(self.engine, ".gitignore"), "__pycache__/\n")
        for args in (["init", "-q"], ["checkout", "-q", "-b", "main"], ["add", "-A"], ["commit", "-qm", "good"],
                     ["remote", "add", "origin", self.origin], ["push", "-q", "-u", "origin", "main"]):
            sh(self.engine, "git", *args)
        self.good = self.head()
        spec = importlib.util.spec_from_file_location("supervisor_t", os.path.join(dc, "supervisor.py"))
        self.sup = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.sup)
        self.dc = dc
        os.makedirs(os.path.join(dc, "state"), exist_ok=True)
        self.sup.POLL, self.sup.BACKOFF, self.sup.LOCK_WAIT = 0.2, 0.5, 2  # the real seconds, scaled down

    def tearDown(self):
        open(os.path.join(self.dc, "state", "stop"), "w").close()
        time.sleep(0.3)
        shutil.rmtree(self.tmp, ignore_errors=True)
        for k, v in self.saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def mode(self, value):
        """The instance's core.update, as the supervisor reads it (update_mode, a copy of update.py's)."""
        inst = os.path.join(self.tmp, "instance")
        write(os.path.join(inst, "plugins.json"), '{"format": 1, "core": {"update": "%s"}}' % value)
        os.environ["ALT_INSTANCE"] = inst

    def hang_remote(self):
        """A remote that never answers: an ssh that sleeps whatever it is given (GIT_SSH_COMMAND="sleep 60" gets
        the host name as a second argument and fails at once, which is not a hang)."""
        fake = os.path.join(self.tmp, "ssh-hangs")
        write(fake, "#!/bin/sh\nexec sleep 60\n")
        os.chmod(fake, 0o755)
        sh(self.engine, "git", "remote", "set-url", "origin", "ssh://nowhere.invalid/x.git")
        os.environ["GIT_SSH_COMMAND"] = "'%s'" % fake

    def head(self):
        return sh(self.engine, "git", "rev-parse", "HEAD").stdout.strip()

    def commit(self, files, msg, push=True):
        for path, text in files.items():
            if text is None:
                sh(self.engine, "git", "rm", "-q", "-r", path)
            else:
                write(os.path.join(self.engine, path), text)
                sh(self.engine, "git", "add", path)
        sh(self.engine, "git", "commit", "-qm", msg)
        if push:
            sh(self.engine, "git", "push", "-q")
        return self.head()

    def good_with_sync(self, src):
        """last_good's altention/sync.py is `src` (the sync child runs the version put back, from disk)."""
        self.good = self.commit({"altention/sync.py": src}, "good, with this sync")

    def break_altention(self):
        return self.commit({"altention/thing.py": "def broken(:\n"}, "bad altention")

    def last_log(self):
        return sh(self.engine, "git", "log", "-1", "--format=%B").stdout

    def notice(self):
        try:
            return slurp(os.path.join(self.dc, "state", "notice.txt"))
        except OSError:
            return ""

    def pushed(self):
        return sh(self.engine, "git", "rev-list", "--count", "@{u}..HEAD").stdout.strip() == "0"

    def run_main(self):
        t = threading.Thread(target=self.sup.main, daemon=True)
        t.start()
        return t

    def wait(self, cond, secs=40):
        until = time.time() + secs
        while time.time() < until:
            if cond():
                return True
            time.sleep(0.2)
        return False

    def ready_for_head(self):
        ready = os.path.join(self.dc, "state", "ready")
        return os.path.exists(ready) and slurp(ready).startswith(self.sup.bot_tree())


class WholeTree(Rig):
    def test_a_stop_flag_of_either_kind_makes_a_cron_started_supervisor_exit_and_stays(self):
        """What ops/apply.py relies on when it leaves state/stop alone (manager 2026-10-06): the supervisor cron starts
        every 5 minutes exits at the top of its loop, starts no bot, and the flag is still there afterwards."""
        stop = os.path.join(self.dc, "state", "stop")
        for content in ("", "uninstalled by apply 2026-10-06T21:00:00+08:00\n"):
            with open(stop, "w") as f:
                f.write(content)
            r = subprocess.run([sys.executable, "supervisor.py"], cwd=self.dc, timeout=30,
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True)
            self.assertEqual(r.returncode, 0, r.stdout)
            self.assertTrue(os.path.exists(stop), "the flag is never removed by the supervisor")
            with open(stop) as f:
                self.assertEqual(f.read(), content)
            self.assertFalse(os.path.exists(os.path.join(self.dc, "state", "bot.pid")), "no bot started")

    def test_the_tree_is_exactly_last_goods_and_every_commit_taken_back_is_listed(self):
        self.commit({"ops/new.py": "N = 1\n", "dcbot/bot/new.py": "N = 1\n"}, "ops and bot files added later")
        bad = self.break_altention()
        self.assertTrue(self.sup.rollback(self.good, "test"))
        self.assertEqual(self.sup.tree(), self.sup.tree(self.good), "the whole tree, added files gone too")
        self.assertFalse(os.path.exists(os.path.join(self.engine, "ops", "new.py")))
        msg = self.last_log()
        self.assertIn("ops and bot files added later", msg)
        self.assertIn(bad[:7], msg)
        self.assertIn("ops and bot files added later", self.notice())
        self.assertTrue(self.pushed())
        self.assertFalse(os.path.exists(os.path.join(self.engine, ".git", "agent-rebase")))

    def test_uncommitted_edits_overlapping_are_saved_others_and_untracked_files_left_alone(self):
        self.commit({"ops/x.py": "X = 1\n"}, "ops change")
        self.break_altention()
        write(os.path.join(self.engine, "ops", "x.py"), "X = 'half done'\n")  # another channel, mid-edit
        write(os.path.join(self.engine, "other.txt"), "an edit not in the rollback\n")
        write(os.path.join(self.engine, "ops", "新的 檔.py"), "untracked\n")
        self.assertTrue(self.sup.rollback(self.good, "test"))
        saved_root = os.path.join(self.engine, ".git", "altention-saved")
        saved = [os.path.join(saved_root, d, "ops", "x.py") for d in os.listdir(saved_root)]
        self.assertEqual(slurp(saved[0]), "X = 'half done'\n", "saved as it was")
        self.assertEqual(slurp(os.path.join(self.engine, "ops", "x.py")), "X = 0\n")
        self.assertEqual(slurp(os.path.join(self.engine, "other.txt")), "an edit not in the rollback\n")
        self.assertEqual(slurp(os.path.join(self.engine, "ops", "新的 檔.py")), "untracked\n")
        self.assertIn("altention-saved", self.notice())
        self.assertIn("不要整個複製回去", self.notice())

    def test_saving_edits_failing_falls_back_to_partial_and_overwrites_nothing(self):
        self.commit({"ops/x.py": "X = 1\n"}, "ops change")
        self.break_altention()
        write(os.path.join(self.engine, "ops", "x.py"), "X = 'half done'\n")
        write(os.path.join(self.engine, ".git", "altention-saved"), "a file where the folder goes\n")
        self.assertTrue(self.sup.rollback(self.good, "test"))
        self.assertEqual(slurp(os.path.join(self.engine, "ops", "x.py")), "X = 'half done'\n")
        self.assertEqual(slurp(os.path.join(self.engine, "altention", "thing.py")), "OK = 1\n")
        self.assertIn("partial", self.last_log())

    def test_a_missing_last_good_is_said_not_silently_skipped(self):
        self.break_altention()
        self.assertFalse(self.sup.rollback("0123456789abcdef0123456789abcdef01234567", "test"))
        self.assertIn("找不到", self.notice())


class Partial(Rig):
    def test_a_move_into_the_instance_in_between_rolls_back_only_dcbot_and_altention(self):
        inst = os.path.join(self.tmp, "instance", "knowledge")
        self.commit({"knowledge/a.md": "old\n"}, "knowledge tracked")
        self.good = self.head()
        write(os.path.join(inst, "a.md"), "the instance's\n")
        sh(self.engine, "git", "rm", "-q", "-r", "knowledge")
        shutil.rmtree(os.path.join(self.engine, "knowledge"), ignore_errors=True)
        with open(os.path.join(self.engine, ".gitignore"), "a") as f:
            f.write("/knowledge\n")
        os.symlink(inst, os.path.join(self.engine, "knowledge"))
        self.commit({".gitignore": slurp(os.path.join(self.engine, ".gitignore"))}, "knowledge moves to the instance")
        self.commit({"dcbot/bot/new.py": "N = 1\n"}, "a bot file added later")
        self.break_altention()
        self.assertTrue(self.sup.rollback(self.good, "test"))
        self.assertTrue(os.path.islink(os.path.join(self.engine, "knowledge")), "the link stands")
        self.assertEqual(slurp(os.path.join(inst, "a.md")), "the instance's\n")
        self.assertIn("/knowledge", slurp(os.path.join(self.engine, ".gitignore")))
        self.assertEqual(self.sup.version(), self.sup.version(self.good))
        self.assertFalse(os.path.exists(os.path.join(self.engine, "dcbot", "bot", "new.py")))
        self.assertIn("partial", self.last_log())
        self.assertIn("部分退回", self.notice())
        self.assertIn(".gitignore", self.notice())


class Remote(Rig):
    def test_the_shared_lock_held_elsewhere_is_waited_for_then_gone_past(self):
        self.break_altention()
        held = open(os.path.join(self.engine, ".git", "altention-git.lock"), "a")
        fcntl.flock(held, fcntl.LOCK_EX)
        self.sup.LOCK_WAIT = 1
        t0 = time.time()
        self.assertTrue(self.sup.rollback(self.good, "test"))
        self.assertGreaterEqual(time.time() - t0, 1)
        self.assertTrue(self.pushed())
        held.close()

    def diverge_remote(self):
        """Someone else pushed a change to the same line the rollback restores: the rollback's rebase conflicts."""
        other = os.path.join(self.tmp, "other")
        sh(self.tmp, "git", "clone", "-q", self.origin, other)
        write(os.path.join(other, "altention", "thing.py"), "OK = 2\n")
        sh(other, "git", "commit", "-qam", "someone else's fix")
        sh(other, "git", "push", "-q")

    def test_with_the_sync_a_conflicting_remote_leaves_the_rollback_here(self):
        self.break_altention()
        self.diverge_remote()
        self.assertTrue(self.sup.rollback(self.good, "test"))
        self.assertFalse(os.path.isdir(os.path.join(self.engine, ".git", "rebase-merge")))
        self.assertEqual(self.sup.tree(), self.sup.tree(self.good))

    def child_fails_and_fallback_runs(self, src):
        self.good_with_sync(src)
        self.break_altention()
        self.sup.SYNC_TIMEOUT = 1.5
        self.assertTrue(self.sup.rollback(self.good, "test"))
        self.assertTrue(self.pushed(), "the plain rebase and push went on")
        self.assertEqual(self.sup.tree(), self.sup.tree(self.good))

    def test_the_sync_child_exiting_is_gone_past(self):
        self.child_fails_and_fallback_runs("import sys\nsys.exit(0)\n")  # even a 0 exit at import: no update()

    def test_the_sync_child_raising_is_gone_past(self):
        self.child_fails_and_fallback_runs("raise RuntimeError('broken sync')\n")

    def test_the_sync_child_hanging_is_gone_past(self):
        self.child_fails_and_fallback_runs("import time\ntime.sleep(60)\n")

    def test_without_the_sync_a_conflicting_rebase_is_undone_and_the_rollback_kept(self):
        self.good_with_sync("raise RuntimeError('broken sync')\n")
        self.break_altention()
        self.diverge_remote()
        self.assertTrue(self.sup.rollback(self.good, "test"))
        gd = os.path.join(self.engine, ".git")
        self.assertFalse(os.path.isdir(os.path.join(gd, "rebase-merge")), "not left mid-rebase")
        self.assertFalse(os.path.exists(os.path.join(gd, "agent-rebase")))
        self.assertEqual(self.sup.version(), self.sup.version(self.good))
        self.assertIn("roll back", self.last_log())

    def test_a_rollback_killed_mid_rebase_is_undone_by_the_next_sync(self):
        self.good_with_sync("raise RuntimeError('broken sync')\n")
        self.break_altention()
        self.diverge_remote()
        real_git = self.sup.git

        def killed_at_abort(*args, **kw):
            if args[0] == "rebase":
                raise SystemExit("killed")  # as if the supervisor died right after `pull --rebase` stopped
            return real_git(*args, **kw)
        self.sup.git = killed_at_abort
        with self.assertRaises(SystemExit):
            self.sup.rollback(self.good, "test")
        self.sup.git = real_git
        gd = os.path.join(self.engine, ".git")
        self.assertTrue(os.path.isdir(os.path.join(gd, "rebase-merge")))
        self.assertTrue(os.path.exists(os.path.join(gd, "agent-rebase")), "marked as ours")
        from altention import sync  # the real engine's: the next sync runs a working altention
        repo = sync.Repo(self.engine)
        with sync.locked(repo):
            why = sync.recover(repo, lambda t: None)
        self.assertIsNone(why, "recovered, not taken for someone rebasing by hand")
        self.assertFalse(os.path.isdir(os.path.join(gd, "rebase-merge")))
        self.assertFalse(os.path.exists(os.path.join(gd, "agent-rebase")))
        self.assertIn("roll back", self.last_log(), "the rollback kept")

    def test_git_hanging_on_the_network_does_not_keep_the_bot_down_and_the_lock_is_let_go(self):
        self.sup.STABLE_SECS, self.sup.TRIAL_SECS = 1, 30
        self.sup.NET_TIMEOUT, self.sup.SYNC_TIMEOUT = 1.5, 1.5
        write(os.path.join(self.dc, "state", "last_good"), self.good)
        self.break_altention()
        self.hang_remote()
        t = self.run_main()
        self.assertTrue(self.wait(self.ready_for_head, 60), "the rolled-back bot came up")
        self.assertIn("roll back", self.last_log())
        lock = open(os.path.join(self.engine, ".git", "altention-git.lock"), "a")
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)  # raises if still held
        lock.close()
        open(os.path.join(self.dc, "state", "stop"), "w").close()
        t.join(10)


class Releases(Rig):
    """C-07: a machine on releases keeps its rollback here: nothing fetched, rebased onto the remote's branch (not
    released) or pushed; the commit is one altention/sync.py's local_commits knows as a rollback."""

    def remote_ahead(self):
        other = os.path.join(self.tmp, "other")
        sh(self.tmp, "git", "clone", "-q", self.origin, other)
        write(os.path.join(other, "ops", "y.py"), "Y = 1\n")
        sh(other, "git", "add", "-A")
        sh(other, "git", "commit", "-qm", "on the branch, not released")
        sh(other, "git", "push", "-q")
        return sh(other, "git", "rev-parse", "HEAD").stdout.strip()

    def test_release_and_notify_keep_the_rollback_here_and_take_nothing(self):
        for value in ("release", "notify"):
            with self.subTest(mode=value):
                self.mode(value)
                bad = self.break_altention()
                ahead = self.remote_ahead()
                shutil.rmtree(os.path.join(self.tmp, "other"))
                self.assertTrue(self.sup.rollback(self.good, "test"))
                self.assertEqual(sh(self.engine, "git", "rev-parse", "HEAD~1").stdout.strip(), bad, "on top of the bad one, here")
                self.assertEqual(self.sup.tree(), self.sup.tree(self.good))
                self.assertNotEqual(sh(self.engine, "git", "rev-parse", "origin/main").stdout.strip(), ahead, "nothing fetched")
                remote = sh(self.origin, "git", "rev-parse", "main").stdout.strip()
                self.assertEqual(remote, ahead, "nothing pushed")
                self.assertIn("core.update 是 %s" % value, self.notice())
                self.assertNotIn("home-git push", self.notice(), "no push asked of a machine on releases")
                from altention import sync
                backs, steps, others = sync.local_commits(sync.Repo(self.engine), bad)
                self.assertEqual((len(backs), steps, others), (1, [], []), "the reader knows the writer's commit")
                self.assertTrue(sync.ROLLBACK_SUBJECT.match(self.last_log().splitlines()[0]))
                os.remove(os.path.join(self.dc, "state", "notice.txt"))
                self.good = self.head()

    def test_a_partial_rollback_is_known_too(self):
        self.mode("release")
        write(os.path.join(self.engine, ".gitignore"), "__pycache__/\nmoved/\n")  # changed in between: partial
        sh(self.engine, "git", "add", ".gitignore")
        bad = self.break_altention()
        self.assertTrue(self.sup.rollback(self.good, "test"))
        self.assertIn("(partial)", self.last_log())
        from altention import sync
        self.assertEqual(len(sync.local_commits(sync.Repo(self.engine), bad)[0]), 1)

    def test_no_instance_seen_and_nothing_read_before_is_notify(self):
        os.environ["ALT_INSTANCE"] = os.path.join(self.tmp, "不在")
        self.assertEqual(self.sup.update_mode()[0], "notify")
        self.break_altention()
        ahead = self.remote_ahead()
        self.assertTrue(self.sup.rollback(self.good, "test"))
        self.assertEqual(sh(self.origin, "git", "rev-parse", "main").stdout.strip(), ahead, "nothing pushed")
        self.assertIn("core.update 是 notify", self.notice())

    def test_a_mode_reading_that_raises_still_rolls_back_and_touches_no_remote(self):
        self.sup.update_mode = lambda: 1 / 0
        self.break_altention()
        ahead = self.remote_ahead()
        self.assertTrue(self.sup.rollback(self.good, "test"))
        self.assertEqual(self.sup.tree(), self.sup.tree(self.good))
        self.assertEqual(sh(self.origin, "git", "rev-parse", "main").stdout.strip(), ahead)


class Trial(Rig):
    def test_the_same_notice_is_not_piled_up_on_every_retry(self):
        self.sup.notice("a")
        self.sup.notice("a")
        self.sup.notice("b")
        self.assertEqual(self.notice(), "a\nb")

    def test_a_requested_rollback_that_raises_says_only_that(self):
        def boom(good, reason):
            raise OSError("disk full")
        self.sup.rollback = boom
        self.assertIsNone(self.sup.safe_rollback(self.good, "使用者要求退回"))
        self.assertIn("退回時出錯", self.notice())

    def test_events_set_aside_count_only_real_failures(self):
        t0 = time.time() - 1
        self.assertEqual(self.sup.failed_events_since(t0), [])
        write(os.path.join(self.dc, "state", "events-bad", "1.net.json"), "{}")
        self.assertEqual(self.sup.failed_events_since(t0), [])
        write(os.path.join(self.dc, "state", "events-bad", "2.json"), "{}")
        self.assertEqual(self.sup.failed_events_since(t0), ["2.json"])

    def test_one_failed_event_and_network_ones_do_not_fail_a_trial(self):
        self.sup.STABLE_SECS, self.sup.TRIAL_SECS = 1, 30
        write(os.path.join(self.dc, "state", "last_good"), self.good)
        new = self.commit({"dcbot/bot/extra.py": "E = 1\n"}, "a new bot version")
        os.environ["FAKE_BAD"] = "1.json,2.net.json,3.net.json"
        t = self.run_main()
        self.assertTrue(self.wait(lambda: self.sup.read("last_good") == new), "marked good")
        self.assertEqual(self.head(), new, "not rolled back")
        open(os.path.join(self.dc, "state", "stop"), "w").close()
        t.join(10)

    def test_two_failed_events_fail_a_trial(self):
        self.sup.STABLE_SECS, self.sup.TRIAL_SECS = 3, 30
        write(os.path.join(self.dc, "state", "last_good"), self.good)
        self.commit({"dcbot/bot/extra.py": "E = 1\n"}, "a new bot version")
        os.environ["FAKE_BAD"] = "1.json,2.json"
        t = self.run_main()
        self.assertTrue(self.wait(lambda: "roll back" in self.last_log()), "rolled back")
        self.assertEqual(self.sup.tree(), self.sup.tree(self.good))
        open(os.path.join(self.dc, "state", "stop"), "w").close()
        t.join(10)


class MainLoop(Rig):
    def test_broken_altention_rolls_back_and_the_bot_comes_up(self):
        self.sup.STABLE_SECS, self.sup.TRIAL_SECS = 1, 30
        write(os.path.join(self.dc, "state", "last_good"), self.good)
        self.break_altention()
        t = self.run_main()
        self.assertTrue(self.wait(lambda: "roll back" in self.last_log() and self.ready_for_head()))
        self.assertEqual(slurp(os.path.join(self.engine, "altention", "thing.py")), "OK = 1\n")
        open(os.path.join(self.dc, "state", "stop"), "w").close()
        t.join(10)

    def test_last_good_follows_head_so_only_the_failed_deploy_is_taken_back(self):
        self.sup.STABLE_SECS, self.sup.TRIAL_SECS, self.sup.FOLLOW_SECS = 1, 30, 0.5
        write(os.path.join(self.dc, "state", "last_good"), self.good)
        t = self.run_main()
        self.assertTrue(self.wait(lambda: self.sup.read("last_good") == self.good and self.ready_for_head()))
        for i in range(1, 4):
            ops = self.commit({"ops/x.py": "X = %d\n" % i}, "ops change %d" % i)
        self.assertTrue(self.wait(lambda: self.sup.read("last_good") == ops), "followed HEAD")
        self.break_altention()
        open(os.path.join(self.dc, "state", "restart-requested"), "w").close()  # the deploy
        self.assertTrue(self.wait(lambda: "roll back" in self.last_log()), "the failed deploy rolled back")
        self.assertEqual(self.sup.tree(), self.sup.tree(ops))
        self.assertEqual(slurp(os.path.join(self.engine, "ops", "x.py")), "X = 3\n", "the ops commits stay")
        self.assertNotIn("ops change", self.last_log())
        open(os.path.join(self.dc, "state", "stop"), "w").close()
        t.join(10)

    def test_a_rollback_raising_does_not_end_the_supervisor_and_the_bot_is_started_again(self):
        self.sup.STABLE_SECS, self.sup.TRIAL_SECS = 1, 30
        write(os.path.join(self.dc, "state", "last_good"), self.good)
        self.break_altention()

        def boom(good, reason):
            raise OSError("disk full")
        self.sup.rollback = boom
        t = self.run_main()
        pids = set()
        self.assertTrue(self.wait(lambda: pids.add(self.sup.read("bot.pid")) or len(pids - {""}) >= 2, 20),
                        "started again after the failed rollback")
        self.assertTrue(t.is_alive())
        self.assertIn("退回時出錯", self.notice())
        open(os.path.join(self.dc, "state", "stop"), "w").close()
        t.join(10)

    def test_a_push_that_cannot_happen_in_time_is_left_here_and_said(self):
        self.break_altention()
        self.sup.DOWN_MAX = 3  # less than any network step needs: none is tried
        self.hang_remote()
        t0 = time.time()
        self.assertTrue(self.sup.rollback(self.good, "test"))
        self.assertLess(time.time() - t0, 10)
        self.assertEqual(self.sup.tree(), self.sup.tree(self.good), "rolled back here all the same")
        self.assertIn("還沒推出去", self.notice())
        self.assertIn("bin/home-git push", self.notice(), "the sync does not push the engine (C-31)")
        self.assertIn("bin/home-pull", self.notice(), "a refused push: pull first")
        self.assertNotIn("doctor", self.notice(), "the owner's word is 系統檢查")

    def test_last_good_does_not_follow_a_new_bot_version_not_yet_restarted(self):
        self.sup.STABLE_SECS, self.sup.FOLLOW_SECS = 1, 0.5
        write(os.path.join(self.dc, "state", "last_good"), self.good)
        t = self.run_main()
        self.assertTrue(self.wait(lambda: self.sup.read("last_good") == self.good and self.ready_for_head()))
        self.commit({"dcbot/bot/extra.py": "E = 1\n"}, "bot change, no restart yet")
        time.sleep(1.5)
        self.assertEqual(self.sup.read("last_good"), self.good)
        open(os.path.join(self.dc, "state", "stop"), "w").close()
        t.join(10)


if __name__ == "__main__":
    unittest.main()


NEEDS_PACKAGES_BOT = FAKE_BOT.replace("import altention.thing", '''import altention.thing
if not os.path.exists(os.environ["FAKE_INSTALLED"]):  # this version needs packages pip has not installed
    sys.exit(1)''')


class PackagesPending(Rig):
    """C-40 (manager 2026-10-17): a new version whose packages are not installed yet (update.py's deps-pending) is
    no bad version. The supervisor installs them first (a fake python: `-m pip` is logged and works only when told
    to, never touching a real environment); when they will not install, the new version failing its trial rolls
    nothing back, commits nothing and pushes nothing (branch mode, the machine just booted), and it is said."""

    def setUp(self):
        super().setUp()
        self.piplog, self.pipok = os.path.join(self.tmp, "pip.log"), os.path.join(self.tmp, "pip-works")
        self.installed = os.path.join(self.tmp, "installed")
        py = os.path.join(self.dc, ".venv", "bin", "python")
        os.remove(py)
        write(py, '#!/bin/sh\nif [ "$1" = "-m" ] && [ "$2" = "pip" ]; then echo "$*" >> "%s"; '
                  '[ -f "%s" ] && { touch "%s"; exit 0; }; echo "error: no compiler"; exit 1; fi\nexec "%s" "$@"\n'
              % (self.piplog, self.pipok, self.installed, sys.executable))
        os.chmod(py, 0o755)
        self.saved_env["FAKE_INSTALLED"] = os.environ.get("FAKE_INSTALLED")
        os.environ["FAKE_INSTALLED"] = self.installed
        self.sup.DEPS_RETRY, self.sup.TRIAL_SECS, self.sup.STABLE_SECS = 2, 6, 1

    def new_version(self):
        """HEAD moved to a version needing packages, and update.py's record for it, as after_move() writes it."""
        new = self.commit({"dcbot/requirements.txt": "discord.py==2.4.0\nnewthing==1.0\n", "dcbot/bot/main.py": NEEDS_PACKAGES_BOT},
                          "a version needing a new package")
        state = os.path.join(os.path.expanduser("~"), ".local", "state", "altention")
        write(os.path.join(state, "deps-pending"), '{"head": "%s", "why": "安裝中…", "at": 1, "next": 0, "tries": 0}' % new)
        sh(self.engine, "git", "push", "-q")
        with open(os.path.join(self.dc, "state", "last_good"), "w") as f:
            f.write(self.good)
        return new, os.path.join(state, "deps-pending")

    def test_the_packages_are_installed_first_and_the_new_version_starts(self):
        open(self.pipok, "w").close()
        new, record = self.new_version()
        self.run_main()
        self.assertTrue(self.wait(self.ready_for_head), self.notice())
        self.assertEqual(self.head(), new, "nothing rolled back")
        self.assertIn("-m pip install -q -r requirements.txt", slurp(self.piplog))
        self.assertFalse(os.path.exists(record), "installed for that HEAD: update.py's record goes")

    def test_packages_that_will_not_install_roll_nothing_back_and_push_nothing(self):
        new, record = self.new_version()
        upstream = sh(self.origin, "git", "rev-parse", "main").stdout.strip()
        self.run_main()  # as cron's @reboot line starts it after the machine came back
        self.assertTrue(self.wait(lambda: "套件還沒裝上" in self.notice()), self.notice())
        self.assertIn("error: no compiler", self.notice())
        self.assertIn("這不算新版壞掉，沒有退回", self.notice())
        time.sleep(1)
        self.assertEqual(self.head(), new, "no rollback commit")
        self.assertEqual(sh(self.origin, "git", "rev-parse", "main").stdout.strip(), upstream, "nothing pushed to main")
        self.assertEqual(sh(self.engine, "git", "status", "--porcelain", "--untracked-files=no").stdout, "", "the tree untouched")
        self.assertTrue(os.path.exists(record), "still pending for update.py and doctor")
        tries = slurp(self.piplog).count("pip install")
        open(self.pipok, "w").close()  # a person ran dcbot/install.sh, or the network came back
        self.assertTrue(self.wait(self.ready_for_head), "the next try installs and the new version comes up")
        self.assertGreater(slurp(self.piplog).count("pip install"), tries)
        self.assertEqual((self.head(), sh(self.origin, "git", "rev-parse", "main").stdout.strip()), (new, upstream))

    def test_a_record_of_another_head_does_not_keep_a_broken_version(self):
        """#code-review on e9622a9: HEAD moved by a path that does not update the record (deploy.sh, a hand pull): pip
        works, the record goes whatever head it names, and a version that is broken all the same is rolled back."""
        open(self.pipok, "w").close()
        new, record = self.new_version()
        bad = self.break_altention()
        self.run_main()
        self.assertTrue(self.wait(lambda: self.head() not in (new, bad) and "roll back" in self.last_log()), self.notice())
        self.assertFalse(os.path.exists(record), "pip worked: the record went")
        self.assertNotIn("套件還沒裝上", self.notice())

    def test_a_record_written_during_the_trial_is_no_reason_not_to_roll_back(self):
        """No pip before this start (no record then), a record written meanwhile for a newer HEAD: the failure is the
        version's own."""
        bad = self.break_altention()
        with open(os.path.join(self.dc, "state", "last_good"), "w") as f:
            f.write(self.good)
        state = os.path.join(os.path.expanduser("~"), ".local", "state", "altention")
        real = self.sup.version

        def version(rev="HEAD"):  # the record appears once the bot has been started
            if os.path.exists(os.path.join(self.dc, "state", "bot.pid")) and not os.path.exists(os.path.join(state, "deps-pending")):
                write(os.path.join(state, "deps-pending"), '{"head": "later", "why": "安裝中…", "at": 1, "next": 0, "tries": 0}')
            return real(rev)
        self.sup.version = version
        self.run_main()
        self.assertTrue(self.wait(lambda: self.head() != bad and "roll back" in self.last_log()), self.notice())
        self.assertNotIn("套件還沒裝上", self.notice())

    def test_a_broken_version_with_its_packages_installed_is_rolled_back_as_before(self):
        open(self.pipok, "w").close()
        new, record = self.new_version()
        bad = self.break_altention()
        with open(record, "w") as f:
            f.write('{"head": "%s", "why": "安裝中…", "at": 1, "next": 0, "tries": 0}' % bad)
        self.run_main()
        self.assertTrue(self.wait(lambda: self.head() not in (new, bad) and "roll back" in self.last_log()), self.notice())


NEW_BOT = NEEDS_PACKAGES_BOT.replace("    sys.exit(1)", """    sys.exit(1)
if os.path.exists(os.environ["FAKE_BROKEN"]):  # broken all the same, its packages there
    sys.exit(1)""")


class StepBack(Rig):
    """C-44 (design: docs/refactor/c44-design.md): pip for a new version failing STEP_BACK_AFTER steps back to
    last_good under a subject of its own, pushed in no mode; the supervisor alone moves forward once pip for the new
    version's requirements works, at once; a move refused puts the old version's packages back ("mixed" when that
    fails too, and then an old version that will not start leads straight to the move forward). The venv's python is
    a fake: `-m pip` logs its arguments and the requirements file it was given, works only when told to, and never
    touches a real environment."""

    def setUp(self):
        super().setUp()
        self.good = self.commit({"dcbot/requirements.txt": "discord.py==2.4.0\n"}, "good, with its requirements")
        d = self.tmp
        self.piplog, self.pipok, self.installed = (os.path.join(d, n) for n in ("pip.log", "pip-works", "installed"))
        self.restore_fails, self.old_down, self.broken = (os.path.join(d, n) for n in ("restore-fails", "old-down", "broken"))
        py = os.path.join(self.dc, ".venv", "bin", "python")
        os.remove(py)
        write(py, """#!/bin/sh
D="%s"
if [ "$1" = "-m" ] && [ "$2" = "pip" ]; then
  echo "$*" >> "$D/pip.log"
  echo "REQ $(tr '\\n' ' ' < "$6")" >> "$D/pip.log"
  if [ -f "$D/pip-works" ] && ! { [ "$6" = requirements.txt ] && [ -f "$D/restore-fails" ]; } \\
     && ! { grep -q newthing "$6" && [ -f "$D/new-fails" ]; }; then
    if grep -q newthing "$6"; then touch "$D/installed"; fi
    exit 0
  fi
  echo "error: no compiler"; exit 1
fi
if [ "$1" = "-m" ] && [ "$2" = "bot.main" ] && [ -f "$D/old-down" ] && ! grep -q newthing requirements.txt; then exit 1; fi
exec "%s" "$@"
""" % (d, sys.executable))
        os.chmod(py, 0o755)
        self.told = os.path.join(d, "told")
        write(os.path.join(self.engine, "bin", "altention"), '#!/bin/sh\necho "$3" >> "%s"\n' % self.told)  # untracked
        os.chmod(os.path.join(self.engine, "bin", "altention"), 0o755)
        for k, v in (("FAKE_INSTALLED", self.installed), ("FAKE_BROKEN", self.broken)):
            self.saved_env[k] = os.environ.get(k)
            os.environ[k] = v
        self.sup.DEPS_RETRY, self.sup.TRIAL_SECS, self.sup.STABLE_SECS, self.sup.STEP_BACK_AFTER = 2, 6, 1, 3

    def new_version(self):
        new = self.commit({"dcbot/requirements.txt": "discord.py==2.4.0\nnewthing==1.0\n", "dcbot/bot/main.py": NEW_BOT},
                          "a version needing a new package")
        self.record = os.path.join(os.path.expanduser("~"), ".local", "state", "altention", "deps-pending")
        write(self.record, '{"head": "%s", "why": "安裝中…", "at": 1, "next": 0, "tries": 0}' % new)
        write(os.path.join(self.dc, "state", "last_good"), self.good)
        self.upstream = sh(self.origin, "git", "rev-parse", "main").stdout.strip()
        return new

    def stepped(self):
        return self.sup.read_json(self.sup.STEPPED)

    def reqs(self):
        """The requirements each pip was given, in order ("new" or "old")."""
        try:
            lines = slurp(self.piplog).splitlines()
        except OSError:
            return []
        return ["new" if "newthing" in l else "old" for l in lines if l.startswith("REQ ")]

    def subject(self):
        return self.last_log().splitlines()[0]

    def step_back(self, mode="branch"):
        self.mode(mode)
        new = self.new_version()
        self.run_main()
        self.assertTrue(self.wait(lambda: self.stepped() and self.ready_for_head(), 60), self.notice())
        return new

    def test_pip_failing_under_the_limit_is_c40_and_counted(self):
        self.sup.STEP_BACK_AFTER = 3600
        new = self.new_version()
        self.run_main()
        self.assertTrue(self.wait(lambda: slurp(self.piplog).count("pip install") >= 2 if os.path.exists(self.piplog) else False))
        self.assertEqual(self.head(), new, "not stepped back yet")
        self.assertIn("這不算新版壞掉，沒有退回", self.notice())
        failing = self.sup.read_json(self.sup.FAILING)
        self.assertEqual(failing["head"], new)
        self.assertGreaterEqual(failing["tries"], 1)

    def test_past_the_limit_it_steps_back_here_and_pushes_nothing_in_branch_mode(self):
        self.stepped_back_in("branch")

    def test_past_the_limit_it_steps_back_here_and_pushes_nothing_in_release_mode(self):
        self.stepped_back_in("release")

    def test_past_the_limit_it_steps_back_here_and_pushes_nothing_in_notify_mode(self):
        self.stepped_back_in("notify")

    def stepped_back_in(self, mode):
        new = self.step_back(mode)
        self.assertRegex(self.subject(), r"^dcbot: step back to %s while packages install \(%s\)$" % (self.good[:7], new[:7]))
        from altention import sync
        self.assertTrue(sync.STEP_BACK_SUBJECT.match(self.subject()), "the reader knows the writer's subject")
        self.assertEqual(sh(self.engine, "git", "rev-parse", "HEAD^").stdout.strip(), new)
        self.assertEqual(self.sup.tree(), self.sup.tree(self.good))
        self.assertEqual(sh(self.origin, "git", "rev-parse", "main").stdout.strip(), self.upstream, "nothing pushed")
        self.assertEqual(sh(self.engine, "git", "rev-parse", "origin/main").stdout.strip(), self.upstream, "nothing fetched")
        rec = self.stepped()
        self.assertEqual((rec["target"], rec["commit"], rec["good"]), (new, self.head(), self.good))
        self.assertIn("先把整個引擎退回 `%s` 用舊版跑" % self.good[:7], self.notice())
        self.assertIn("不要推", self.notice())
        self.assertNotIn("home-git push", self.notice())
        self.assertEqual(slurp(self.told).count("先用舊版回來了"), 1, "told once")
        time.sleep(1.5)
        self.assertEqual(self.sup.read("last_good"), self.good, "a step back never becomes last_good")
        self.assertTrue(self.wait(lambda: self.reqs()[-1:] == ["old"]), "the old version's packages at its start")

    def test_pip_working_moves_forward_at_once_and_the_new_version_comes_up(self):
        new = self.step_back()
        step = self.head()
        n = len(self.reqs())
        open(self.pipok, "w").close()
        self.assertTrue(self.wait(lambda: self.head() == new and self.ready_for_head(), 60), self.notice())
        self.assertEqual(self.reqs()[n], "new", "pip for the new version's requirements, not the work tree's")
        self.assertIsNone(self.stepped())
        self.assertFalse(os.path.exists(self.record), "update.py's record goes with it")
        self.assertNotEqual(sh(self.engine, "git", "merge-base", "--is-ancestor", step, "HEAD").returncode, 0, "the step back is gone")
        self.assertEqual(sh(self.engine, "git", "rev-list", "--count", "%s..HEAD" % new).stdout.strip(), "0", "no new commit")
        self.assertTrue(self.wait(lambda: self.sup.read("last_good") == new))

    def test_the_new_version_broken_after_moving_forward_is_rolled_back_pushed_and_its_packages_put_back(self):
        new = self.step_back()
        open(self.broken, "w").close()
        open(self.pipok, "w").close()
        self.assertTrue(self.wait(lambda: "roll back" in self.last_log(), 60), self.notice())
        self.assertEqual(sh(self.engine, "git", "rev-parse", "HEAD^").stdout.strip(), new)
        self.assertEqual(self.sup.tree(), self.sup.tree(self.good))
        self.assertTrue(self.wait(self.pushed), "branch mode: the rollback of a broken version is pushed (after its commit)")
        self.assertTrue(self.wait(lambda: self.reqs()[-1:] == ["old"]), "pip for the tree put back")
        self.assertTrue(self.wait(self.ready_for_head))

    def test_head_moved_away_by_the_sync_drops_the_record_and_resets_nothing(self):
        self.step_back()
        other = os.path.join(self.tmp, "other")
        sh(self.tmp, "git", "clone", "-q", self.origin, other)
        write(os.path.join(other, "ops", "y.py"), "Y = 1\n")
        sh(other, "git", "add", "-A")
        sh(other, "git", "commit", "-qm", "upstream moved on")
        sh(other, "git", "push", "-q")
        sh(self.engine, "git", "fetch", "-q")
        sh(self.engine, "git", "reset", "-q", "--keep", "origin/main")  # what sync.update does with a step back
        moved = self.head()
        n = len(self.reqs())
        self.assertTrue(self.wait(lambda: self.stepped() is None, 20))
        time.sleep(1)
        self.assertEqual(self.head(), moved)
        self.assertEqual(self.reqs()[n:], [], "no pip for a step back that is gone")

    def test_a_refused_move_puts_the_old_packages_back_and_is_tried_again(self):
        """Items 6 and 8: overlapping edits refuse the move after pip installed the new version's packages: pip again
        at once with the work tree's (the old version's) requirements, the edits kept, said once; the edits gone, the
        next round moves forward."""
        new = self.step_back()
        step = self.head()
        main_py = os.path.join(self.dc, "bot", "main.py")
        write(main_py, slurp(main_py) + "# another channel's edit\n")
        n = len(self.reqs())
        open(self.pipok, "w").close()
        self.assertTrue(self.wait(lambda: (self.stepped() or {}).get("blocked"), 30), self.notice())
        self.assertEqual(self.reqs()[n:n + 2], ["new", "old"], "the old version's packages back at once")
        self.assertEqual((self.stepped() or {}).get("venv"), "")
        self.assertEqual(self.head(), step)
        self.assertIn("another channel's edit", slurp(main_py))
        self.assertIn("回到新版沒成功", self.notice())
        self.assertIn("舊版照常跑", self.notice())
        time.sleep(2.5)
        self.assertEqual(self.notice().count("回到新版沒成功"), 1, "said once")
        pid = self.sup.read("bot.pid")
        sh(self.engine, "git", "checkout", "--", "dcbot/bot/main.py")
        self.assertTrue(self.wait(lambda: self.head() == new and self.ready_for_head(), 60), self.notice())
        self.assertNotEqual(self.sup.read("bot.pid"), pid)

    def test_a_mixed_venv_leads_an_old_version_that_will_not_start_straight_to_the_move(self):
        new = self.step_back()
        main_py = os.path.join(self.dc, "bot", "main.py")
        write(main_py, slurp(main_py) + "# another channel's edit\n")
        open(self.restore_fails, "w").close()
        open(self.pipok, "w").close()
        self.assertTrue(self.wait(lambda: (self.stepped() or {}).get("venv") == "mixed", 30), self.notice())
        self.assertIn("舊版重啟時可能起不來", self.notice())
        self.assertEqual(slurp(self.told).count("舊版重啟時可能起不來"), 1)
        open(self.old_down, "w").close()  # the old version on the mixed venv will not start
        n = len(self.reqs())
        os.kill(int(self.sup.read("bot.pid")), 9)
        self.assertTrue(self.wait(lambda: "起不來（套件是新舊混合的）" in self.notice(), 30), self.notice())
        self.assertIn("new", self.reqs()[n:], "the move forward tried at once")
        self.assertEqual(slurp(self.told).count("起不來（套件是新舊混合的）"), 1)
        sh(self.engine, "git", "checkout", "--", "dcbot/bot/main.py")
        self.assertTrue(self.wait(lambda: self.head() == new and self.ready_for_head(), 60), self.notice())

    def test_no_last_good_stays_down_as_c40(self):
        new = self.new_version()
        os.remove(os.path.join(self.dc, "state", "last_good"))
        self.run_main()
        self.assertTrue(self.wait(lambda: os.path.exists(self.piplog) and slurp(self.piplog).count("pip install") >= 4, 60))
        self.assertEqual(self.head(), new)
        self.assertIsNone(self.stepped())
        self.assertNotIn("step back", self.last_log())

    def test_commits_on_top_of_the_step_back_keep_the_record_and_are_said(self):
        """#code-review of 88e7229: a commit on top of the step back (a deploy, a commit by hand) is no step back the
        sync left behind: the record stays (doctor keeps telling), said once, nothing reset; the step back taken
        out of the history, the record goes."""
        self.step_back()
        step = self.head()
        self.commit({"ops/z.py": "Z = 1\n"}, "a commit on top", push=False)
        top = self.head()
        open(self.pipok, "w").close()
        self.assertTrue(self.wait(lambda: "上面疊了別的 commit" in self.notice(), 20), self.notice())
        n = len(self.reqs())  # tries before the commit on top ran pip for the new version (and failed)
        time.sleep(2.5)
        self.assertEqual(self.head(), top, "nothing reset")
        self.assertEqual((self.stepped() or {}).get("commit"), step, "the record stays")
        self.assertEqual(self.notice().count("上面疊了別的 commit"), 1, "said once")
        self.assertEqual(self.reqs()[n:], [], "no pip for a move that cannot happen")
        sh(self.engine, "git", "rebase", "-q", "--onto", step + "^", step)  # what SUPERVISOR-RESCUE.md says
        self.assertTrue(self.wait(lambda: self.stepped() is None, 20), "out of the history: the record goes")

    def test_a_step_back_left_behind_loses_its_records_at_the_next_start_even_a_trial(self):
        """#code-review-2 of 88e7229 (manager 2026-10-17): update.py left the step back behind on moving to a newer
        version and restarted the bot into its trial; step_forward never ran then, and the record stayed (doctor told
        of a step back long gone). Every start, trial or not, drops the records of a step back not in HEAD's history."""
        self.step_back()
        self.sup.DEPS_RETRY = 100  # step_forward out of the way: only a start may drop it
        pid = self.sup.read("bot.pid")
        write(os.path.join(self.dc, "state", "restart-requested"), '{"channel": ""}\n')
        self.assertTrue(self.wait(lambda: self.sup.read("bot.pid") not in ("", pid) and self.ready_for_head()))
        self.assertIsNotNone(self.stepped(), "still in HEAD's history: kept")
        other = os.path.join(self.tmp, "other")
        sh(self.tmp, "git", "clone", "-q", self.origin, other)
        write(os.path.join(other, "dcbot", "requirements.txt"), "discord.py==2.4.0\n")
        write(os.path.join(other, "dcbot", "bot", "main.py"), FAKE_BOT)
        sh(other, "git", "add", "-A")
        sh(other, "git", "commit", "-qm", "v1.3: the new package not needed after all")
        sh(other, "git", "push", "-q")
        sh(self.engine, "git", "fetch", "-q")
        sh(self.engine, "git", "reset", "-q", "--keep", "origin/main")  # what sync.update does with a step back
        write(os.path.join(self.dc, "state", "restart-requested"), '{"channel": ""}\n')  # update.py's flag
        self.assertTrue(self.wait(lambda: self.stepped() is None, 15), "dropped at the trial's start")
        self.assertIsNone(self.sup.read_json(self.sup.FAILING))
        self.assertTrue(self.wait(self.ready_for_head))

    def x_on_top_of_the_step_back(self):
        """A commit X of someone's on top of the step back S, changing dcbot/: X's own trial at the next start."""
        self.step_back()
        step = self.head()
        write(os.path.join(self.dc, "state", "last_good"), self.good)
        x = self.commit({"dcbot/bot/extra.py": "E = 1\n"}, "X on top of the step back", push=False)
        return step, x

    def test_a_rollback_with_a_step_back_under_it_is_made_here_and_not_pushed(self):
        """#code-review of 60c6eec (manager 2026-10-17): X's trial fails, the rollback is made (the bot back on a
        good version) but not pushed: S would go with it. Then X good and a later version rolled back to X: the same."""
        step, x = self.x_on_top_of_the_step_back()
        from altention import sync
        self.assertEqual(self.sup.STEP_BACK_SUBJECT.pattern, sync.STEP_BACK_SUBJECT.pattern, "the copy in the supervisor")
        open(self.pipok, "w").close()  # X's packages install (its requirements are the old version's): its own trial decides
        os.environ["FAKE_BAD"] = "a.json,b.json"
        self.saved_env.setdefault("FAKE_BAD", None)
        write(os.path.join(self.dc, "state", "restart-requested"), '{"channel": ""}\n')
        self.assertTrue(self.wait(lambda: "roll back" in self.last_log(), 60), self.notice())
        os.environ.pop("FAKE_BAD")
        self.assertEqual(sh(self.engine, "git", "rev-parse", "HEAD^").stdout.strip(), x)
        self.assertIn("壓在下面，沒推", self.notice())
        self.assertIn("bin/home-git rebase --onto %s^ %s" % (step[:7], step[:7]), self.notice())
        time.sleep(2)
        self.assertEqual(sh(self.origin, "git", "rev-parse", "main").stdout.strip(), self.upstream, "the origin received nothing")
        self.assertTrue(self.wait(self.ready_for_head), "the bot back on the version put back")

    def test_a_rollback_to_a_good_version_on_top_of_a_step_back_is_not_pushed(self):
        step, x = self.x_on_top_of_the_step_back()
        write(os.path.join(self.dc, "state", "last_good"), x)  # X came up and was marked good
        self.break_altention_unpushed = self.commit({"altention/thing.py": "def broken(:\n"}, "bad altention", push=False)
        self.assertTrue(self.sup.rollback(x, "test"))
        self.assertEqual(self.sup.tree(), self.sup.tree(x))
        self.assertIn("壓在下面，沒推", self.notice())
        self.assertNotIn("還沒推出去（網路或衝突）", self.notice())
        self.assertEqual(sh(self.origin, "git", "rev-parse", "main").stdout.strip(), self.upstream, "the origin received nothing")
        self.assertEqual(sh(self.engine, "git", "rev-parse", "origin/main").stdout.strip(), self.upstream, "nothing fetched either")

    def test_a_step_back_alone_taken_out_by_hand_installs_the_new_packages_before_the_trial(self):
        self.taken_out_by_hand("alone on top")

    def test_a_step_back_under_a_commit_taken_out_by_hand_installs_the_new_packages_before_the_trial(self):
        self.taken_out_by_hand("under a commit")

    def taken_out_by_hand(self, how):
        """Manager 2026-10-17 (#code-review-2 of 60c6eec): the old version's start had installed its own packages (the
        deps-pending record gone); the step back taken out as SUPERVISOR-RESCUE.md says, alone on top or under a
        commit of someone's, then !restart: pip for the new requirements before the trial (state/venv-requirements
        differs), and pip failing rolls nothing back and pushes nothing (C-40)."""
        new = self.step_back()
        step = self.head()
        open(self.pipok, "w").close()
        open(os.path.join(self.tmp, "new-fails"), "w").close()  # the old packages install, the new ones do not
        pid = self.sup.read("bot.pid")
        write(os.path.join(self.dc, "state", "restart-requested"), '{"channel": ""}\n')
        self.assertTrue(self.wait(lambda: self.sup.read("bot.pid") not in ("", pid) and self.ready_for_head()))
        self.assertTrue(self.wait(lambda: not os.path.exists(self.record)), "the old version's start installed its packages")
        if how == "under a commit":
            self.commit({"ops/z.py": "Z = 1\n"}, "someone's commit on top", push=False)
        sh(self.engine, "git", "rebase", "-q", "--onto", step + "^", step)  # SUPERVISOR-RESCUE.md
        self.assertNotEqual(sh(self.engine, "git", "merge-base", "--is-ancestor", step, "HEAD").returncode, 0)
        top = self.head()
        n = len(self.reqs())
        write(os.path.join(self.dc, "state", "restart-requested"), '{"channel": ""}\n')  # !restart
        self.assertTrue(self.wait(lambda: "new" in self.reqs()[n:], 30), self.notice())
        self.assertTrue(self.wait(lambda: "`%s` 啟動不了，它要的套件還沒裝上" % top[:7] in self.notice(), 30), self.notice())
        self.assertEqual(self.reqs()[n], "new", "pip for the new requirements before the trial")
        time.sleep(2)
        self.assertEqual(self.head(), top, "nothing rolled back")
        self.assertNotIn("roll back", self.last_log())
        self.assertEqual(sh(self.origin, "git", "rev-parse", "main").stdout.strip(), self.upstream, "nothing pushed")
        self.assertEqual(self.sup.read("last_good"), self.good)
