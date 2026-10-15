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
        self.saved_env = {k: os.environ.get(k) for k in ("GIT_SSH_COMMAND", "FAKE_BAD")}
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
        held = open(os.path.join(self.engine, ".git", "agent-git.lock"), "a")
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
        lock = open(os.path.join(self.engine, ".git", "agent-git.lock"), "a")
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)  # raises if still held
        lock.close()
        open(os.path.join(self.dc, "state", "stop"), "w").close()
        t.join(10)


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

    def test_a_push_that_cannot_happen_in_time_is_left_for_the_sync_and_said(self):
        self.break_altention()
        self.sup.DOWN_MAX = 3  # less than any network step needs: none is tried
        self.hang_remote()
        t0 = time.time()
        self.assertTrue(self.sup.rollback(self.good, "test"))
        self.assertLess(time.time() - t0, 10)
        self.assertEqual(self.sup.tree(), self.sup.tree(self.good), "rolled back here all the same")
        self.assertIn("還沒推出去", self.notice())

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
