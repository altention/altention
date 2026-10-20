"""altention/update.py (3a.10): the engine moves only behind a syntax check; a fast-forward or, with local commits,
the sync's update() in a child; the lock, a rebase underway, another checkout skip a round; whatever goes wrong is
written to the alerts file first and the old version runs on; it imports nothing of the engine; either of it and the
sync keeps the fixes coming when the other is down."""
import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))))
from altention import testing as _testing  # noqa: E402
_testing.guard_home()  # tests never touch the real home (altention/testing.py)
import ast
import fcntl
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from unittest import mock

from altention import sync, update

ENGINE_REAL = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
STDLIB = {"fcntl", "json", "os", "pwd", "re", "shutil", "signal", "subprocess", "sys", "tempfile", "time"}


def git(path, *args, check=True):
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com", "-C", path] + list(args),
                          check=check, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)


def branch_mode(home, mode="branch"):
    """The mode last read (update-mode) as update.py writes it: what update_mode() takes when no instance is seen."""
    folder = os.path.join(home, ".local", "state", "altention")
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, "update-mode"), "w") as f:
        json.dump({"mode": mode, "at": "2026-10-14T00:00:00+0000"}, f)


class Rig(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="更新 ")
        self.origin = os.path.join(self.tmp, "origin.git")
        git(self.tmp, "init", "-q", "--bare", self.origin)
        git(self.origin, "symbolic-ref", "HEAD", "refs/heads/main")
        self.up = os.path.join(self.tmp, "up")  # where new versions are made
        os.makedirs(self.up)
        shutil.copytree(os.path.join(ENGINE_REAL, "altention"), os.path.join(self.up, "altention"),
                        ignore=shutil.ignore_patterns("tests", "__pycache__"))
        self.write(self.up, "tool.py", "X = 1\n")
        for args in (["init", "-q"], ["checkout", "-q", "-b", "main"], ["add", "-A"], ["commit", "-qm", "one"],
                     ["remote", "add", "origin", self.origin], ["push", "-q", "-u", "origin", "main"]):
            git(self.up, *args)
        self.engine = os.path.join(self.tmp, "engine")
        git(self.tmp, "clone", "-q", self.origin, self.engine)
        git(self.engine, "config", "user.name", "t")  # the machines have one; a rebase needs it
        git(self.engine, "config", "user.email", "t@example.com")
        self.patch = mock.patch.object(update, "ENGINE", self.engine)
        self.patch.start()
        # the sync's "is the engine a checkout" asked of this test's engine, not of the tree the tests run from (an
        # archive has no .git: the engine would not be synced at all)
        real_is_checkout = sync.is_checkout
        self.checkout = mock.patch.object(sync, "is_checkout", lambda engine=None: real_is_checkout(engine or self.engine))
        self.checkout.start()
        self.state = update.state_dir()
        shutil.rmtree(self.state, ignore_errors=True)
        branch_mode(os.path.expanduser("~"))  # these test what follows the branch: the mode our machines keep
        self.told, self.where = [], []
        self.notify = mock.patch.object(update.subprocess, "run", self.fake_run)
        self.real_run = subprocess.run

    def tearDown(self):
        self.patch.stop()
        self.checkout.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)
        shutil.rmtree(self.state, ignore_errors=True)

    def fake_run(self, argv, *a, **k):
        if argv and str(argv[0]).endswith("altention") and argv[1:3] == ["dc", "notify"]:
            self.told.append(argv[-1])
            self.where.append(argv[argv.index("-c") + 1] if "-c" in argv else "")
            return subprocess.CompletedProcess(argv, 0)
        return self.real_run(argv, *a, **k)

    def write(self, repo, rel, text):
        p = os.path.join(repo, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w") as f:
            f.write(text)

    def publish(self, rel, text, msg="next"):
        self.write(self.up, rel, text)
        git(self.up, "add", "-A")
        git(self.up, "commit", "-qm", msg)
        git(self.up, "push", "-q")
        return git(self.up, "rev-parse", "HEAD").stdout.strip()

    def head(self):
        return git(self.engine, "rev-parse", "HEAD").stdout.strip()

    def alerts(self):
        try:
            with open(os.path.join(self.state, "alerts")) as f:
                return f.read()
        except OSError:
            return ""

    def round(self):
        with self.notify:
            return update.one_round()


class Moves(Rig):
    def test_a_fast_forward_after_the_check(self):
        new = self.publish("tool.py", "X = 2\n")
        self.assertEqual(self.round(), 0)
        self.assertEqual(self.head(), new)
        self.assertIsNotNone(update.last_ok("update"))
        self.assertEqual(self.alerts(), "")

    def test_a_syntax_error_upstream_does_not_move_and_is_told(self):
        old = self.head()
        self.publish("tool.py", "def broken(:\n")
        self.assertEqual(self.round(), 1)
        self.assertEqual(self.head(), old, "the branch did not move")
        self.assertIn("update\tcheck\t", self.alerts())
        self.assertIn("沒通過檢查", self.told[0])
        self.assertIsNone(update.last_ok("update"))

    def test_the_always_checked_files_even_when_not_changed(self):
        """sync.py broken upstream in an earlier commit nobody pulled yet is caught too (always in the list)."""
        old = self.head()
        self.publish("altention/sync.py", "def x(:\n", "breaks sync")
        self.publish("tool.py", "X = 3\n")
        self.assertEqual(self.round(), 1)
        self.assertEqual(self.head(), old)

    def test_a_shell_script_that_does_not_parse(self):
        old = self.head()
        self.publish("x/install.sh", "if then fi (\n")
        self.assertEqual(self.round(), 1)
        self.assertEqual(self.head(), old)
        self.assertIn("bash -n", self.alerts())

    def test_this_machines_own_python_is_the_one_checking(self):
        self.publish("tool.py", "X = 4\n")
        seen = []
        real = subprocess.run

        def spy(argv, *a, **k):
            if argv[:2] == [sys.executable, "-c"]:
                seen.append(argv[0])
            return self.fake_run(argv, *a, **k)
        with mock.patch.object(update.subprocess, "run", spy):
            update.one_round()
        self.assertEqual(set(seen), {sys.executable}, "the load check and the compile check: this machine's python3")

    def test_edits_that_do_not_overlap_carry_over_overlapping_ones_stop_it(self):
        self.write(self.engine, "mine.txt", "a channel's edit\n")
        new = self.publish("tool.py", "X = 5\n")
        self.assertEqual(self.round(), 0)
        self.assertEqual(self.head(), new)
        self.assertTrue(os.path.exists(os.path.join(self.engine, "mine.txt")))
        self.write(self.engine, "tool.py", "X = 'being edited'\n")
        newer = self.publish("tool.py", "X = 6\n")
        self.assertEqual(self.round(), 1)
        self.assertEqual(self.head(), new, "not moved over an edit in the way")
        self.assertIn("update\tff\t", self.alerts())
        with open(os.path.join(self.engine, "tool.py")) as f:
            self.assertEqual(f.read(), "X = 'being edited'\n", "the edit is untouched")

    def test_an_ignored_file_of_the_same_name_stops_the_move(self):
        """C-66 (manager): git overwrites an ignored file on a fast-forward without a word; one the person ignores
        with the name of a file the new version adds stops the round, as one not tracked does."""
        with open(os.path.join(self.engine, ".git", "info", "exclude"), "a") as f:
            f.write("notes-of-mine.txt\n")
        self.write(self.engine, "notes-of-mine.txt", "mine\n")
        old = self.head()
        self.publish("notes-of-mine.txt", "the engine's\n")
        self.assertEqual(self.round(), 1)
        self.assertEqual(self.head(), old)
        with open(os.path.join(self.engine, "notes-of-mine.txt")) as f:
            self.assertEqual(f.read(), "mine\n", "not overwritten")
        self.assertIn("update\tin-the-way\t", self.alerts())
        self.assertIn("你自己的檔（notes-of-mine.txt）跟新版要加的檔同名", self.told[-1])

    def test_what_was_in_the_way_is_said_in_plain_words(self):
        """C-57: a file not in the engine with a name the new version adds was told as an overlapping edit; now
        each cause in its own words, naming the file, and the technical part in brackets."""
        self.write(self.engine, "new.txt", "mine, not the engine's\n")
        old = self.head()
        self.publish("new.txt", "the engine's new file\n")
        self.assertEqual(self.round(), 1)
        self.assertEqual(self.head(), old)
        self.assertIn("你自己的檔（new.txt）跟新版要加的檔同名，這次先不更新", self.told[-1], self.told)  # C-66: before the move
        self.assertIn("到 #setup 跟 Claude 說「引擎更新卡住了，幫我處理」", self.told[-1])
        self.assertNotIn("修改", self.told[-1].split("（細節")[0])
        with open(os.path.join(self.engine, "new.txt")) as f:
            self.assertEqual(f.read(), "mine, not the engine's\n")
        os.remove(os.path.join(self.engine, "new.txt"))
        self.round()
        self.write(self.engine, "tool.py", "X = 'being edited'\n")
        self.publish("tool.py", "X = 31\n")
        with mock.patch.object(update, "TELL_EVERY", 0):
            self.assertEqual(self.round(), 1)
        self.assertIn("你自己改過的檔（tool.py），新版本也改了同一個檔", self.told[-1], self.told)
        for word in ("存", "commit"):
            self.assertNotIn(word, self.told[-1].split("（細節：")[0], "plain words, not an engineer's")
        self.assertIn("你的修改都還在", self.told[-1])
        for t in self.told:
            self.assertTrue(t.startswith("⚠️ 引擎更新："), t)
        self.assertEqual(set(self.where), {"update"}, "C-57: to #update")

    def test_a_detached_engine_says_once_that_it_does_not_update(self):
        """C-57: it used to say nothing, ever, even to --release."""
        git(self.engine, "checkout", "-q", "--detach")
        old = self.head()
        self.publish("tool.py", "X = 32\n")
        for argv in ([], [], ["--release"]):
            out = io.StringIO()
            with self.notify, redirect_stdout(out):
                self.assertEqual(update.main(argv), 0)
            self.assertIn("固定的版本", out.getvalue())
        self.assertEqual(self.head(), old)
        self.assertEqual(len(self.told), 1, self.told)
        self.assertIn("不會自己更新", self.told[0])
        self.assertEqual(self.where, ["update"])
        git(self.engine, "commit", "-q", "--allow-empty", "-m", "elsewhere")  # HEAD changed: said again
        self.round()
        self.assertEqual(len(self.told), 2)

    def test_the_sync_stopping_goes_to_the_default_channel(self):
        with self.notify:
            update.alert("update", "sync-stale", "同步停了")
            update.alert("update", "check", "沒通過")
        self.assertEqual(self.where, ["", "update"], "not about updating: the default channel")

    def test_local_commits_go_through_the_syncs_update_in_a_child(self):
        self.write(self.engine, "local.txt", "a commit of this machine\n")
        git(self.engine, "add", "-A")
        git(self.engine, "commit", "-qm", "local")
        new = self.publish("tool.py", "X = 7\n")
        self.assertEqual(self.round(), 0, self.alerts())
        log = git(self.engine, "log", "--format=%s", "-3").stdout.split("\n")
        self.assertEqual(log[0], "local", "rebased on top")
        self.assertEqual(git(self.engine, "merge-base", "--is-ancestor", new, "HEAD", check=False).returncode, 0)

    def test_a_child_that_takes_too_long_is_killed_whole_and_told(self):
        self.write(self.engine, "local.txt", "x\n")
        git(self.engine, "add", "-A")
        git(self.engine, "commit", "-qm", "local")
        old = self.head()
        self.publish("tool.py", "X = 8\n")
        marker = os.path.join(self.tmp, "child-still-there")
        with open(os.path.join(self.engine, "altention", "sync.py"), "a") as f:  # the child's update() hangs
            f.write("\n\ndef update(repo):\n    import subprocess, time\n"
                    "    subprocess.Popen(['sh', '-c', 'sleep 3; touch %s'])\n    time.sleep(60)\n" % marker)
        t0 = time.time()
        with mock.patch.object(update, "CHILD_TIMEOUT", 1):
            self.assertEqual(self.round(), 1)
        self.assertLess(time.time() - t0, 30)
        self.assertEqual(self.head(), old)
        self.assertIn("update\trebase\t", self.alerts())
        self.assertIn("沒有結束，已中止", self.alerts())
        time.sleep(4)
        self.assertFalse(os.path.exists(marker), "the whole session was killed: nothing it started lived on")

    def test_nothing_new_is_fine_and_marked(self):
        self.assertEqual(self.round(), 0)
        self.assertIsNotNone(update.last_ok("update"))


class Fixes(Rig):
    """#code-review on bc22105 (manager 2026-10-06)."""

    def test_alt_tmp_with_someone_elses_files_is_left_alone(self):
        shared = os.path.join(self.tmp, "shared tmp")
        os.makedirs(shared)
        theirs = os.path.join(shared, "theirs.txt")
        with open(theirs, "w") as f:
            f.write("not ours\n")
        self.publish("tool.py", "X = 20\n")
        with mock.patch.dict(os.environ, {"ALT_TMP": shared}):
            self.assertEqual(self.round(), 0)
        self.assertTrue(os.path.exists(theirs), "only its own folder is ever removed")
        self.assertEqual(sorted(os.listdir(shared)), [update.CHECK_DIR, "theirs.txt"])
        self.assertEqual(os.listdir(os.path.join(shared, update.CHECK_DIR)), [], "its round folder removed")

    def test_a_tool_without_an_extension_is_checked_by_its_first_line(self):
        old = self.head()
        self.publish("dcbot/tools/dchook", "#!/usr/bin/env python3\ndef broken(:\n")
        self.assertEqual(self.round(), 1)
        self.assertEqual(self.head(), old)
        old = self.head()
        self.publish("bin/other-tool", "#!/bin/sh\nif then (\n")
        self.assertEqual(self.round(), 1)
        self.assertEqual(self.head(), old)

    def test_a_broken_told_record_does_not_stop_the_telling(self):
        os.makedirs(self.state, exist_ok=True)
        with open(os.path.join(self.state, "alerts-told.json"), "w") as f:
            json.dump({"update/x": "not a number"}, f)
        with self.notify:
            update.alert("update", "x", "仍然通知")
        self.assertEqual(len(self.told), 1)

    def test_update_broken_the_sync_still_moves_the_engine_and_says_so(self):
        calls = []
        with mock.patch.object(update, "last_ok", side_effect=SyntaxError("update.py broken")), \
                mock.patch.object(sync, "sync_repo", side_effect=lambda *a, **k: calls.append(k.get("pull")) or {"ok": True, "error": None, "at": 0}), \
                mock.patch.object(sync.config, "instance_dir", return_value=None):
            st = sync.run()
        self.assertEqual(calls, [True])
        self.assertEqual(st["engine_pulled_by"], "sync")
        self.assertIn("sync\tupdate-broken\t", self.alerts())

    def test_a_push_refused_while_update_owns_the_engine_waits_quietly(self):
        with mock.patch.object(sync, "push", return_value=(False, "push 失敗：! [rejected] main -> main (fetch first)")), \
                mock.patch.object(sync, "recover", return_value=None):
            r = sync.sync_repo(self.engine, [], "m", lambda t: None, pull=False)
        self.assertTrue(r["ok"], r)
        self.assertIn("等 update.py", r["waiting"])
        with mock.patch.object(sync, "push", return_value=(False, "push 失敗：! [rejected] main -> main (fetch first)")), \
                mock.patch.object(sync, "recover", return_value=None), mock.patch.object(sync, "update", return_value=(0, None)):
            r = sync.sync_repo(self.engine, [], "m", lambda t: None, pull=True)
        self.assertFalse(r["ok"], "pulling itself, a refused push is a failure as before")

    def test_an_exception_in_a_round_is_written_down(self):
        with mock.patch.object(update, "one_round", side_effect=UnicodeDecodeError("utf-8", b"x", 0, 1, "bad")), self.notify:
            self.assertEqual(update.main([]), 1)
        self.assertIn("update\terror\t", self.alerts())


class Skips(Rig):
    def test_the_lock_taken_skips_the_round(self):
        new = self.publish("tool.py", "X = 9\n")
        old = self.head()
        with open(os.path.join(self.engine, ".git", update.LOCK_NAME), "a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            self.assertEqual(self.round(), 0)
        self.assertEqual(self.head(), old)
        self.round()
        self.assertEqual(self.head(), new)

    def test_release_asked_for_with_the_lock_held_says_so_by_its_exit(self):
        """C-57 U5 (manager): update.py --release that did nothing because the git lock was held exits 75, so
        ops/apply.py tries again instead of installing on the old engine; a plain round still 0."""
        self.publish("tool.py", "X = 9\n")
        old = self.head()
        with open(os.path.join(self.engine, ".git", update.LOCK_NAME), "a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            with self.notify, redirect_stdout(io.StringIO()):
                self.assertEqual(update.main(["--release"]), update.LOCKED)
                self.assertEqual(update.main([]), 0, "cron's round: next round, as before")
        self.assertEqual(self.head(), old)

    def test_a_rebase_underway_skips(self):
        self.publish("tool.py", "X = 10\n")
        old = self.head()
        os.makedirs(os.path.join(self.engine, ".git", "rebase-merge"))
        self.assertEqual(self.round(), 0)
        self.assertEqual(self.head(), old)

    def test_another_checkout_is_left_alone(self):
        wt = os.path.join(self.tmp, "wt")
        git(self.engine, "worktree", "add", "-q", wt)
        with mock.patch.object(update, "ENGINE", wt), mock.patch.dict(os.environ, {"ALT_TEST_ANY_ENGINE": ""}):
            self.assertEqual(update.verdict()[0], "other")
            self.assertEqual(self.round(), 0)


    def test_an_unpacked_engine_is_said_and_the_round_counts_as_done(self):
        """No .git at all (an archive unpacked): nothing to update from, said every round, never an alert."""
        unpacked = os.path.join(self.tmp, "解開 的 engine")
        shutil.copytree(self.up, unpacked, ignore=shutil.ignore_patterns(".git"))
        out = io.StringIO()
        with mock.patch.object(update, "ENGINE", unpacked), self.notify, redirect_stdout(out):
            self.assertEqual(update.verdict()[0], "ok")
            self.assertEqual(update.main([]), 0)
        self.assertIn("不是 git checkout", out.getvalue())
        self.assertTrue(out.getvalue().rstrip().endswith("ALT-UPDATE-DONE"))
        self.assertEqual((self.alerts(), self.told), ("", []))
        self.assertTrue(update.last_ok("update"), "the round counts as done: the sync does not take over")
        self.assertFalse(os.path.exists(os.path.join(unpacked, ".git")))


class Alerts(Rig):
    def test_written_first_kept_to_a_limit_told_once_in_six_hours(self):
        with self.notify, mock.patch.object(update, "ALERTS_KEPT", 3):
            for i in range(5):
                update.alert("update", "x%d" % i, "第 %d 則" % i)
        lines = self.alerts().splitlines()
        self.assertEqual(len(lines), 3)
        self.assertTrue(lines[-1].endswith("第 4 則"))
        self.assertEqual(len(self.told), 5, "once per kind in six hours")

    def test_two_kinds_in_turn_keep_one_line_each(self):
        """#code-review-2: two kinds written in turn 120 times each filled the file; one source and kind keeps one
        row, wherever its last one was."""
        with self.notify:
            update.alert("sync", "fetch", "別的事", notify=False)
            for i in range(120):
                update.alert("a", "k1", "一 %d" % i, notify=False)
                update.alert("b", "k2", "二 %d" % i, notify=False)
        lines = self.alerts().splitlines()
        self.assertEqual(len(lines), 3)
        self.assertIn("別的事", lines[0])
        self.assertTrue(lines[1].endswith("一 119（共 120 次）"), lines[1])
        self.assertTrue(lines[2].endswith("二 119（共 120 次）"), lines[2])

    def test_one_kind_repeating_takes_one_line_counted(self):
        """Manager 2026-10-07: the same source and kind as the last line replaces it (time, text, count), so one
        kind repeating never pushes the others out of the file."""
        with self.notify:
            update.alert("sync", "fetch", "別的事")
            for i in range(30):
                update.alert("update", "alive-unreadable", "讀不出 %d" % i, notify=False)
        lines = self.alerts().splitlines()
        self.assertEqual(len(lines), 2)
        self.assertIn("別的事", lines[0])
        self.assertTrue(lines[1].endswith("讀不出 29（共 30 次）"), lines[1])
        rows = []
        for mod in (update, __import__("altention.sync", fromlist=["x"])):
            rows = mod.add_alert(rows, "t", "a", "k", "x")
        self.assertEqual(rows, ["t\ta\tk\tx（共 2 次）\n"], "the same rule in sync.py")

    def test_a_told_record_that_cannot_be_written_tells_every_time(self):
        os.makedirs(self.state, exist_ok=True)
        os.makedirs(os.path.join(self.state, "alerts-told.json.tmp"))  # a folder: the record cannot be written
        with self.notify:
            update.alert("update", "y", "a")
            update.alert("update", "y", "b")
        self.assertEqual(len(self.told), 2)

    def test_a_dcnotify_that_fails_still_leaves_the_line(self):
        with mock.patch.object(update.subprocess, "run", side_effect=OSError("no dcnotify")):
            update.alert("update", "z", "看得到嗎")
        self.assertIn("看得到嗎", self.alerts())


class EitherKeepsFixesComing(Rig):
    """Kill one, the other still moves the engine (manager 2026-10-06)."""

    def test_sync_down_update_still_pulls(self):
        new = self.publish("tool.py", "X = 11\n")
        update.mark_ok("sync")
        with open(os.path.join(self.state, "last_ok-sync"), "w") as f:
            json.dump({"at": time.time() - 3 * 3600}, f)  # the sync stopped hours ago
        with self.notify:
            self.assertEqual(update.main([]), 0)
        self.assertEqual(self.head(), new)
        self.assertIn("sync-stale", self.alerts(), "and it says the sync stopped")

    def test_update_down_sync_pulls_and_says_so(self):
        calls = []
        os.makedirs(update.state_dir(), exist_ok=True)
        with open(os.path.join(update.state_dir(), "last_ok-update"), "w") as f:
            json.dump({"at": time.time() - 2 * 3600}, f)  # update.py ran before, and stopped two hours ago
        with mock.patch.object(sync, "sync_repo", side_effect=lambda *a, **k: calls.append(k.get("pull")) or {"ok": True, "error": None, "at": 0}), \
                mock.patch.object(sync.config, "instance_dir", return_value=None), \
                mock.patch.object(update.subprocess, "run", self.fake_run):
            sync.run()
        self.assertEqual(calls, [True], "the sync moves the engine itself")
        self.assertIn("sync\tupdate-stale\t", self.alerts())

    def test_three_rounds_count_at_the_interval_apply_wrote(self):
        """C-37 C: the sync once an hour (sync.interval) is not stale after 50 minutes, nor is the sync taken to stand
        in for an update.py that runs every 30; without intervals.json, or with one that cannot be used, 45 minutes."""
        os.makedirs(update.state_dir(), exist_ok=True)
        path = os.path.join(update.state_dir(), "intervals.json")
        self.assertEqual((update.stale_of("sync"), update.stale_of("engine-update")), (45 * 60, 45 * 60))
        for bad in ('{"sync": "1h"}', '{"sync": true}', '{"sync": 5}', "[1]", "{ half"):
            with open(path, "w") as f:
                f.write(bad)
            self.assertEqual(update.stale_of("sync"), 45 * 60, bad)
        with open(path, "w") as f:
            json.dump({"sync": 3600, "engine-update": 1800}, f)
        self.assertEqual((update.stale_of("sync"), update.stale_of("engine-update")), (3 * 3600, 90 * 60))
        with open(os.path.join(self.state, "last_ok-sync"), "w") as f:
            json.dump({"at": time.time() - 50 * 60}, f)
        update.watch_sync()
        self.assertNotIn("sync-stale", self.alerts(), "50 minutes is under three of its rounds")
        with open(os.path.join(update.state_dir(), "last_ok-update"), "w") as f:
            json.dump({"at": time.time() - 60 * 60}, f)
        calls = []
        with mock.patch.object(sync, "sync_repo", side_effect=lambda *a, **k: calls.append(k.get("pull")) or {"ok": True, "error": None, "at": 0}), \
                mock.patch.object(sync.config, "instance_dir", return_value=None):
            sync.run()
        self.assertEqual(calls, [False], "an hour is under three rounds of a 30-minute update.py: it is left to it")

    def test_update_alive_sync_leaves_the_engine_to_it(self):
        update.mark_ok("update")
        calls = []
        with mock.patch.object(sync, "sync_repo", side_effect=lambda *a, **k: calls.append(k.get("pull")) or {"ok": True, "error": None, "at": 0}), \
                mock.patch.object(sync.config, "instance_dir", return_value=None):
            st = sync.run()
        self.assertEqual(calls, [False])
        self.assertEqual(st["engine_pulled_by"], "update")
        self.assertEqual(self.alerts(), "")

    def test_never_run_yet_sync_pulls_quietly(self):
        calls = []
        with mock.patch.object(sync, "sync_repo", side_effect=lambda *a, **k: calls.append(k.get("pull")) or {"ok": True, "error": None, "at": 0}), \
                mock.patch.object(sync.config, "instance_dir", return_value=None):
            sync.run()
        self.assertEqual(calls, [True])
        self.assertEqual(self.alerts(), "", "no update.py yet: nothing to say")
        self.assertIsNotNone(update.last_ok("sync"), "the sync marks its own")


class Imports(unittest.TestCase):
    def test_standard_library_only_nothing_of_the_engine(self):
        """As the supervisor: a bug anywhere else in the engine must not stop the fixes from arriving. sync.update()
        is reached only in a child process (the code string)."""
        with open(os.path.join(ENGINE_REAL, "altention", "update.py"), encoding="utf-8") as f:
            tree = ast.parse(f.read())
        names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names |= {a.name.split(".")[0] for a in node.names}
            elif isinstance(node, ast.ImportFrom):
                names.add((node.module or "").split(".")[0])
        self.assertEqual(names - STDLIB, set())

    def test_its_rules_agree_with_engine_id_and_the_supervisor(self):
        """Three copies of one rule set: the same cases, the same answers."""
        import importlib.util
        from altention import config, engine_id
        spec = importlib.util.spec_from_file_location("sup_u", os.path.join(ENGINE_REAL, "dcbot", "supervisor.py"))
        sup = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(sup)
        home = tempfile.mkdtemp(prefix="家 ")
        try:
            with mock.patch.dict(os.environ, {"HOME": home, "ALT_TEST_ANY_ENGINE": "", "ALT_ROOT": ""}):
                main = os.path.join(home, "altention", "engine")  # the default place
                os.makedirs(main)
                git(main, "init", "-q")
                git(main, "commit", "-q", "--allow-empty", "-m", "x")
                wt = os.path.join(home, "wt")
                git(main, "worktree", "add", "-q", wt)
                second = os.path.join(home, "second")
                git(home, "clone", "-q", main, second)
                same = {"official": "ok", "other": "other", "unknown": "wait"}
                launchers = []
                for name in ("cron_update.py", "cron_sync.py"):
                    sp = importlib.util.spec_from_file_location("l_" + name[:-3], os.path.join(ENGINE_REAL, "altention", name))
                    lm = importlib.util.module_from_spec(sp)
                    sp.loader.exec_module(lm)
                    launchers.append(lm)
                for path in (main, wt, second):
                    with mock.patch.object(config, "inventory", return_value={}), mock.patch.object(update, "ENGINE", path), \
                            mock.patch.object(sup, "ENGINE", path):
                        a = engine_id.check(path)[0]
                        self.assertEqual(update.verdict()[0], same[a], path)
                        self.assertEqual(sup.engine_verdict()[0], same[a], path)
                        for lm in launchers:
                            with mock.patch.object(lm, "ENGINE", path):
                                self.assertEqual(bool(lm.elsewhere()), a == "other", (lm.__name__, path))
        finally:
            shutil.rmtree(home, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()


class BrokenSomewhere(Rig):
    """Manager 2026-10-06 (C): three ways to break (does not compile, throws when loaded, throws while running), on
    update.py, sync.py and the two cron launchers; each time the next commit that fixes it still arrives, and the
    alerts file has a line. The broken file is made to arrive as if through an earlier round."""
    BREAK = {"syntax": "def broken(:\n", "load": "raise RuntimeError('broken on load')\n"}

    def arrive(self, rel, text):
        with open(os.path.join(self.up, rel)) as f:
            self.good = f.read()
        self.publish(rel, text, "breaks %s" % rel)
        git(self.engine, "fetch", "-q")
        git(self.engine, "merge", "-q", "--ff-only", "@{u}")  # it got here (say, through the sync's own pull)
        return self.publish(rel, self.good, "fixes %s" % rel)

    def broken(self, rel, how):
        if how == "runtime":  # loads fine, throws once at work
            target = {"altention/update.py": "def one_round(force_release=False):\n", "altention/sync.py": "def main(argv):\n",
                      "altention/cron_update.py": "def main():\n", "altention/cron_sync.py": "def main():\n"}[rel]
            with open(os.path.join(self.up, rel)) as f:
                text = f.read()
            return text.replace(target, target + "    raise RuntimeError('broken at work')\n", 1)
        with open(os.path.join(self.up, rel)) as f:
            return self.BREAK[how] + f.read()

    def launch(self, which):
        return subprocess.run([sys.executable, os.path.join(self.engine, "altention", which)], stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT, universal_newlines=True, timeout=600,
                              env=dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.com",
                                       GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.com"))

    def stale(self, name):
        os.makedirs(self.state, exist_ok=True)
        with open(os.path.join(self.state, "last_ok-%s" % name), "w") as f:
            json.dump({"at": time.time() - 3 * 3600}, f)

    def case(self, rel, how, launcher, stale=None):
        fix = self.arrive(rel, self.broken(rel, how))
        if stale:
            self.stale(stale)
        before = self.alerts()
        r = self.launch(launcher)
        self.assertEqual(self.head(), fix, "%s %s via %s: the fix did not arrive\n%s" % (rel, how, launcher, r.stdout[-500:]))
        self.assertNotEqual(self.alerts(), before, "%s %s via %s: nothing written down" % (rel, how, launcher))

    def test_update_py_broken_each_way(self):
        for how in ("syntax", "load", "runtime"):
            with self.subTest(how=how):
                self.case("altention/update.py", how, "cron_update.py")

    def test_sync_py_broken_each_way(self):
        for how in ("syntax", "load", "runtime"):
            with self.subTest(how=how):
                self.case("altention/sync.py", how, "cron_sync.py")

    def test_update_py_broken_the_sync_launcher_still_brings_the_fix(self):
        for how in ("syntax", "load"):
            with self.subTest(how=how):
                self.case("altention/update.py", how, "cron_sync.py")

    def test_the_update_launcher_broken_the_sync_brings_the_fix(self):
        for how in ("syntax", "load", "runtime"):
            with self.subTest(how=how):
                self.case("altention/cron_update.py", how, "cron_sync.py", stale="update")

    def test_the_sync_launcher_broken_update_brings_the_fix(self):
        for how in ("syntax", "load", "runtime"):
            with self.subTest(how=how):
                self.case("altention/cron_sync.py", how, "cron_update.py", stale="sync")

    def test_output_that_is_not_utf8_still_falls_back(self):
        """A round that prints bytes that are not UTF-8 (an odd file name) and dies: the launcher still reaches the
        fallback (#code-review 2026-10-06)."""
        bad = "import sys\nsys.stdout.buffer.write(b'\\xff\\xfe broken name\\n')\nsys.exit(1)\n"
        fix = self.arrive("altention/update.py", bad)
        r = self.launch("cron_update.py")
        self.assertEqual(self.head(), fix, r.stdout)
        self.assertIn("cron-update\tfallback\t", self.alerts())

    def test_a_commit_that_throws_when_loaded_never_arrives_through_update(self):
        old = self.head()
        self.publish("altention/config.py", "raise RuntimeError('on load')\n" + open(os.path.join(self.up, "altention/config.py")).read())
        r = self.launch("cron_update.py")
        self.assertEqual(self.head(), old, r.stdout)
        self.assertIn("載入檢查沒過", self.alerts())


class Modes(BrokenSomewhere):
    """The instance's core.update (manager 2026-10-14): notify (the default) moves nothing and writes the newer
    release down; release takes only vX.Y.Z tags; branch is what it always was (the other classes here). No path
    that moves the engine by itself takes the branch unless the mode is branch."""

    def setUp(self):
        super().setUp()
        self.inst = os.path.join(self.tmp, "實例 instance")
        os.makedirs(self.inst)
        self.env = mock.patch.dict(os.environ, {"ALT_INSTANCE": self.inst})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        super().tearDown()

    def mode(self, value):
        with open(os.path.join(self.inst, "plugins.json"), "w") as f:
            json.dump({"format": 1, "core": {"update": value}}, f)

    def tag(self, name, commit=None):
        git(self.up, "tag", name, commit or "HEAD")
        git(self.up, "push", "-q", "origin", name)

    def available(self):
        try:
            with open(os.path.join(self.state, "update-available")) as f:
                return json.load(f)
        except OSError:
            return None

    def test_notify_moves_nothing_and_writes_the_newer_release_down(self):
        self.mode("notify")
        old = self.head()
        self.publish("tool.py", "X = 2\n")
        self.tag("v0.1.0")
        out = io.StringIO()
        with self.notify, redirect_stdout(out):
            self.assertEqual(update.main([]), 0)
        self.assertEqual(self.head(), old, "notify takes nothing")
        self.assertEqual(self.available()["tag"], "v0.1.0")
        self.assertIn("有新版 v0.1.0", out.getvalue())
        self.assertEqual(self.alerts(), "", "not a fault")
        self.assertEqual(len(self.told), 1, "C-18: the new release pushed out once")
        self.assertIn("v0.1.0", self.told[0])
        self.assertIn("更新引擎", self.told[0])
        self.assertIn("已推播", out.getvalue())
        for _ in range(2):
            self.round()
        self.assertEqual(len(self.told), 1, "the same release: once")
        self.assertEqual(self.available()["told"], "v0.1.0")
        git(self.engine, "merge", "-q", "--ff-only", "v0.1.0")  # a person took it
        self.round()
        self.assertIsNone(self.available(), "nothing newer: the note goes")

    def test_notify_tells_each_new_release_and_tries_again_when_the_notice_fails(self):
        self.mode("notify")
        self.publish("tool.py", "X = 21\n")
        self.tag("v0.5.0")
        failing = []

        def notify_fails(argv, *a, **k):
            if argv and str(argv[0]).endswith("altention") and argv[1:3] == ["dc", "notify"]:
                failing.append(argv[-1])
                return subprocess.CompletedProcess(argv, 1)
            return self.real_run(argv, *a, **k)
        with mock.patch.object(update.subprocess, "run", notify_fails):
            update.one_round()
        self.assertEqual((len(failing), self.available()["told"]), (1, None), "not out: not marked")
        self.round()
        self.assertEqual(len(self.told), 1, "tried again, out now")
        self.publish("tool.py", "X = 22\n")
        self.tag("v0.6.0")
        self.round()
        self.assertEqual(len(self.told), 2)
        self.assertIn("v0.6.0", self.told[1])
        self.round()
        self.assertEqual(len(self.told), 2)

    def test_release_asked_for_takes_the_newest_release_in_notify_mode(self):
        """The person asked to update (update.py --release): this round takes the newest release, the branch never,
        and core.update stays notify for the rounds after (owner's update by hand, 2026-10-15)."""
        self.mode("notify")
        rel = self.publish("tool.py", "X = 11\n")
        self.tag("v0.3.0")
        self.publish("tool.py", "X = 12\n")  # on the branch, not released
        out = io.StringIO()
        with self.notify, redirect_stdout(out):
            self.assertEqual(update.main(["--release"]), 0)
        self.assertEqual(self.head(), rel, "the newest release, not the branch")
        self.assertIn("更新了", out.getvalue())
        self.assertEqual(update.update_mode(), ("notify", ""), "the setting is left as it is")
        self.assertIsNone(self.available())
        self.publish("tool.py", "X = 13\n")
        self.tag("v0.4.0")
        self.round()
        self.assertEqual(self.head(), rel, "the next plain round is notify again: nothing taken")
        with self.notify, redirect_stdout(io.StringIO()):
            self.assertEqual(update.main(["--bogus"]), 2)

    def test_notify_is_the_default_without_the_key(self):
        with open(os.path.join(self.inst, "plugins.json"), "w") as f:
            json.dump({"format": 1, "core": {"language": "zh-TW"}}, f)
        old = self.head()
        self.publish("tool.py", "X = 3\n")
        self.round()
        self.assertEqual(self.head(), old)
        self.assertEqual(update.update_mode(), ("notify", ""))

    def test_release_takes_the_newest_tag_never_the_branch(self):
        self.mode("release")
        first = self.publish("tool.py", "X = 4\n")
        self.tag("v0.9.0")
        second = self.publish("tool.py", "X = 5\n")
        self.tag("v0.10.0")  # newer than v0.9.0 by number, not by text
        self.publish("tool.py", "X = 6\n")  # on the branch, not released
        self.round()
        self.assertEqual(self.head(), second, "the newest release, not the branch")
        self.assertNotEqual(self.head(), first)
        self.assertIsNone(self.available())
        self.round()
        self.assertEqual(self.head(), second, "nothing newer: stays")

    def test_release_without_any_tag_stays(self):
        self.mode("release")
        old = self.head()
        self.publish("tool.py", "X = 7\n")
        self.assertEqual(self.round(), 0)
        self.assertEqual(self.head(), old)

    def test_release_refuses_a_tag_that_does_not_pass_the_check(self):
        self.mode("release")
        old = self.head()
        self.publish("altention/x.py", "def broken(:\n")
        self.tag("v1.0.0")
        self.assertEqual(self.round(), 1)
        self.assertEqual(self.head(), old)
        self.assertIn("update\tcheck\t", self.alerts())

    def test_release_puts_local_commits_on_top_of_the_tag(self):
        self.mode("release")
        self.write(self.engine, "mine.txt", "local\n")
        git(self.engine, "add", "mine.txt")
        git(self.engine, "commit", "-qm", "local")
        rel = self.publish("tool.py", "X = 8\n")
        self.tag("v0.2.0")
        self.publish("tool.py", "X = 9\n")  # not released
        self.assertEqual(self.round(), 0, self.alerts())
        self.assertEqual(git(self.engine, "rev-parse", "HEAD~1").stdout.strip(), rel, "on the release, not the branch")
        self.assertTrue(os.path.exists(os.path.join(self.engine, "mine.txt")))

    def test_settings_unreadable_keep_the_mode_last_read_and_say_so(self):
        self.mode("notify")
        self.round()
        with open(os.path.join(self.inst, "plugins.json"), "w") as f:
            f.write("{ a comma too many, }")
        mode, problem = update.update_mode()
        self.assertEqual(mode, "notify")
        self.assertIn("plugins.json", problem)
        self.round()
        self.assertIn("update\tmode\t", self.alerts())
        branch_mode(os.path.expanduser("~"))  # had the last round read branch
        self.assertEqual(update.update_mode()[0], "branch", "a broken file never stops a branch machine's fixes")

    def test_the_fallbacks_never_take_the_branch_unless_branch(self):
        for mode in ("notify", "release"):
            for launcher, rel in (("cron_update.py", "altention/update.py"), ("cron_sync.py", "altention/sync.py")):
                with self.subTest(mode=mode, launcher=launcher):
                    self.mode(mode)
                    fix = self.arrive(rel, self.broken(rel, "syntax"))
                    broken = self.head()
                    r = self.launch(launcher)
                    self.assertEqual(self.head(), broken, "%s via %s: took %s\n%s" % (mode, launcher, fix, r.stdout[-400:]))
                    self.assertIn("core.update 是 %s" % mode, r.stdout + self.alerts())
                    git(self.engine, "fetch", "-q")
                    git(self.engine, "merge", "-q", "--ff-only", fix)  # put right for the next case

    def test_the_sync_never_stands_in_unless_branch(self):
        for mode in ("notify", "release"):
            with self.subTest(mode=mode):
                self.mode(mode)
                for when in (time.time() - 2 * 3600, None):  # update.py stopped two hours ago; never ran
                    path = os.path.join(update.state_dir(), "last_ok-update")
                    if when is None:
                        if os.path.exists(path):
                            os.remove(path)
                    else:
                        os.makedirs(update.state_dir(), exist_ok=True)
                        with open(path, "w") as f:
                            json.dump({"at": when}, f)
                    calls = []
                    with mock.patch.object(sync, "sync_repo", side_effect=lambda *a, **k: calls.append(k.get("pull")) or {"ok": True, "error": None, "at": 0}), \
                            mock.patch.object(sync.config, "instance_dir", return_value=None), \
                            mock.patch.object(update.subprocess, "run", self.fake_run):
                        st = sync.run()
                    self.assertEqual(calls, [False], (mode, when))
                    self.assertEqual(st["engine_pulled_by"], "update")
                self.assertIn("sync\tupdate-stale-held\t", self.alerts())
                self.assertNotIn("由同步代為更新引擎", self.alerts())


class UpdatedNotice(Rig):
    """C-55: a machine that moves by itself (release, branch) says once per release that it now runs it, whoever
    moved HEAD; the first round writes down what it runs and says nothing; notify mode as it was."""

    def setUp(self):
        super().setUp()
        self.inst = os.path.join(self.tmp, "實例 instance")
        os.makedirs(self.inst)
        self.env = mock.patch.dict(os.environ, {"ALT_INSTANCE": self.inst})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        super().tearDown()

    def mode(self, value):
        with open(os.path.join(self.inst, "plugins.json"), "w") as f:
            json.dump({"format": 1, "core": {"update": value}}, f)

    def release(self, version, points=("Something new. More about it.",), x=None):
        """A commit whose CHANGELOG has a section for `version` with these points, tagged and pushed."""
        text = "# Changelog\n\n## v%s — 2026-10-20\n\n### For people\n\n%s\n\n### For agents\n\n- Agents only.\n" % (
            version, "\n".join("- " + p.replace(". ", ".\n  ", 1) for p in points))
        self.write(self.up, "CHANGELOG.md", text)
        commit = self.publish("tool.py", "X = %r\n" % (x or version))
        git(self.up, "tag", "v" + version)
        git(self.up, "push", "-q", "origin", "v" + version)
        return commit

    def record(self):
        try:
            with open(os.path.join(self.state, "updated-release")) as f:
                return json.load(f)
        except OSError:
            return None

    def notices(self):
        return [t for t in self.told if t.startswith("✅")]

    def test_release_mode_stops_on_an_ignored_file_in_the_way(self):
        """C-66: the automatic round to a release, with a file the person ignores that the release adds."""
        self.mode("release")
        self.release("0.1.0")
        self.round()
        with open(os.path.join(self.engine, ".git", "info", "exclude"), "a") as f:
            f.write("mine.cfg\n")
        self.write(self.engine, "mine.cfg", "mine\n")
        old = self.head()
        self.write(self.up, "mine.cfg", "the release's\n")
        self.release("0.2.0")
        self.assertEqual(self.round(), 1)
        self.assertEqual(self.head(), old)
        with open(os.path.join(self.engine, "mine.cfg")) as f:
            self.assertEqual(f.read(), "mine\n")
        self.assertEqual(self.notices(), [], "no ✅: not moved")
        self.assertEqual(git(self.engine, "tag", "-l", "altention/before-v0.2.0").stdout, "", "no mark for a move not made")

    def test_first_round_writes_down_what_it_runs_and_says_nothing(self):
        for mode in ("release", "branch"):
            with self.subTest(mode=mode):
                shutil.rmtree(self.state, ignore_errors=True)
                del self.told[:]
                self.mode(mode)
                self.release("0.1.%d" % ("release", "branch").index(mode))
                self.round()
                self.assertEqual(self.notices(), [], "a new install or this code arriving: nothing pushed")
                self.assertEqual(self.record()["tag"], "v0.1.%d" % ("release", "branch").index(mode))
                self.round()
                self.assertEqual(self.notices(), [])

    def test_a_new_release_is_told_once_with_its_points(self):
        self.mode("release")
        self.release("0.1.0")
        self.round()
        self.release("0.2.0", ("The board reads limits. It costs one request.", "Backups are asked about."))
        self.round()
        self.assertEqual(len(self.notices()), 1, self.told)
        n = self.notices()[0]
        self.assertTrue(n.startswith("✅ 引擎已更新到 v0.2.0"), n)
        self.assertIn("- The board reads limits.", n)
        self.assertIn("- Backups are asked about.", n)
        self.assertNotIn("It costs one request", n, "the first sentence only")
        self.assertIn("完整的在 CHANGELOG.md 的 v0.2.0 一節", n)
        self.assertNotIn("Agents only", n, "For people only")
        self.assertEqual(self.record()["tag"], "v0.2.0")
        for _ in range(2):
            self.round()
        self.assertEqual(len(self.notices()), 1, "once per release")

    def test_head_moved_by_someone_else_is_told_too(self):
        for mode in ("branch", "release"):
            with self.subTest(mode=mode):
                shutil.rmtree(self.state, ignore_errors=True)
                del self.told[:]
                self.mode(mode)
                self.round()
                self.assertEqual(self.record(), {"tag": None, "at": self.record()["at"]} if mode == "branch" else self.record())
                tag = self.release("0.%d.0" % (3 + ("branch", "release").index(mode)), ("Moved by hand.",))
                git(self.engine, "fetch", "-q", "--tags")
                git(self.engine, "merge", "-q", "--ff-only", tag)  # home-pull, the sync standing in, a push here
                self.round()
                self.assertEqual(len(self.notices()), 1, (mode, self.told))
                self.assertIn("- Moved by hand.", self.notices()[0])

    def test_no_release_yet_then_the_first_one_is_told(self):
        self.mode("branch")
        self.round()
        self.assertIsNone(self.record()["tag"])
        self.release("0.1.0")
        self.round()
        self.assertEqual(len(self.notices()), 1)
        self.assertIn("v0.1.0", self.notices()[0])

    def test_several_at_once_tells_the_newest_and_names_the_ones_between(self):
        self.mode("release")
        self.release("0.1.0")
        self.round()
        for v in ("0.2.0", "0.9.0", "0.10.0"):  # by number, not by text
            self.release(v, ("Point of %s." % v,), x=v + "x")
        self.round()
        self.assertEqual(len(self.notices()), 1, self.told)
        n = self.notices()[0]
        self.assertTrue(n.startswith("✅ 引擎已更新到 v0.10.0（中間的 v0.2.0、v0.9.0 也一起裝上了）"), n)
        self.assertIn("Point of 0.10.0.", n)
        self.assertNotIn("Point of 0.9.0.", n, "the newest one's notes only")

    def test_a_notice_that_fails_is_tried_again_next_round(self):
        self.mode("release")
        self.release("0.1.0")
        self.round()
        self.release("0.2.0")
        failing = []

        def notify_fails(argv, *a, **k):
            if argv and str(argv[0]).endswith("altention") and argv[1:3] == ["dc", "notify"]:
                failing.append(argv[-1])
                return subprocess.CompletedProcess(argv, 1)
            return self.real_run(argv, *a, **k)
        with mock.patch.object(update.subprocess, "run", notify_fails):
            update.one_round()
        self.assertEqual(len(failing), 1)
        self.assertEqual(self.record()["tag"], "v0.1.0", "not out: not written down")
        self.round()
        self.assertEqual(len(self.notices()), 1)
        self.assertEqual(self.record()["tag"], "v0.2.0")

    def test_head_going_back_and_forward_again_tells_nothing(self):
        self.mode("branch")
        old = self.release("0.1.0")
        self.round()
        self.release("0.2.0")
        self.round()
        self.assertEqual(len(self.notices()), 1)
        git(self.engine, "reset", "-q", "--hard", old)  # a rollback, a step back
        with mock.patch.object(update, "check_tree", return_value="held back by the test"):
            self.round()
        self.assertEqual(self.record()["tag"], "v0.2.0")
        self.round()
        self.assertEqual(len(self.notices()), 1, self.told)

    def stack(self, subject):
        """A commit of the supervisor's on top of HEAD, as put_back() or a step back makes it here (never pushed)."""
        git(self.engine, "revert", "--no-commit", "HEAD")
        git(self.engine, "commit", "-qm", subject)

    def test_a_rollback_or_step_back_on_top_waits_until_it_is_gone(self):
        for mode, subject in (("branch", "dcbot: roll back to abcdef0 (trial failed)"),
                              ("release", "dcbot: roll back (partial) to abcdef0 (trial failed)"),
                              ("branch", "dcbot: step back to abcdef0 while packages install (1234567)"),
                              ("release", "dcbot: step back to abcdef0 while packages install (1234567)")):
            with self.subTest(mode=mode, subject=subject):
                shutil.rmtree(self.state, ignore_errors=True)
                del self.told[:]
                self.mode(mode)
                start = self.release("0.%d.0" % (len(self.notices()) + 2 * len(subject) + (mode == "release")))
                self.round()
                was = self.record()["tag"]
                tag = "v0.%d.1" % (2 * len(subject) + (mode == "release"))
                new = self.release(tag[1:], ("Shiny new thing.",))
                git(self.engine, "fetch", "-q", "--tags")
                git(self.engine, "merge", "-q", "--ff-only", new)  # home-pull, or the sync standing in
                self.stack(subject)  # the trial failed: the supervisor took it back
                for _ in range(2):
                    self.round()
                self.assertEqual(self.notices(), [], "it is not what runs")
                self.assertEqual(self.record()["tag"], was, "the record stays")
                git(self.engine, "reset", "-q", "--hard", new)  # taken out, the fixed version in
                self.round()
                self.assertEqual(len(self.notices()), 1, self.told)
                self.assertIn(tag, self.notices()[0])
                self.assertNotEqual(start, new)

    def test_its_rollback_subject_is_the_syncs(self):
        self.assertEqual(update.ROLLBACK_SUBJECT.pattern, sync.ROLLBACK_SUBJECT.pattern, "the copy in update.py")

    def test_the_real_changelogs_points_are_cut_to_their_lead(self):
        """v0.2.1's own section: a bold lead is the whole of its line, else the first sentence; none too long."""
        with open(os.path.join(ENGINE_REAL, "CHANGELOG.md")) as f:
            self.write(self.up, "CHANGELOG.md", f.read())
        self.publish("tool.py", "X = 'real'\n")
        git(self.up, "tag", "v0.2.1")
        git(self.up, "push", "-q", "origin", "v0.2.1")
        git(self.engine, "fetch", "-q", "--tags")
        lines, cut = update.release_notes(update.Round(), "v0.2.1")
        self.assertEqual(lines, [
            "**When a new version's packages will not install, the bot steps back instead of staying down.**",
            "**Restart the machine once after updating**",
            "The bot's first start under the new supervisor runs `pip install` once, even when its packages did not "
            "change: the supervisor now records which requirements it installed and checks them before every start.",
            "**A board is no longer posted twice.**"])
        self.assertTrue(cut)
        self.assertTrue(all(len(l) <= update.NOTE_CHARS for l in lines))
        self.publish("CHANGELOG.md", "## v0.2.2 — x\n\n- %s\n" % ("y" * 400))
        git(self.up, "tag", "v0.2.2")
        git(self.up, "push", "-q", "origin", "v0.2.2")
        git(self.engine, "fetch", "-q", "--tags")
        lines, cut = update.release_notes(update.Round(), "v0.2.2")
        self.assertEqual((len(lines[0]), lines[0][-1], cut), (update.NOTE_CHARS, "…", True))

    def test_notify_mode_says_nothing_but_an_asked_for_release_is_told(self):
        """C-57: the update the person asked for (--release) is told too, from the release HEAD had before it (a
        notify machine keeps no record); the plain notify rounds as they were."""
        self.mode("notify")
        self.release("0.1.0")
        self.round()
        with self.notify, redirect_stdout(io.StringIO()):
            update.main(["--release"])
        self.assertEqual(self.record()["tag"], "v0.1.0")
        self.assertEqual([t[0] for t in self.told], ["🆕", "✅"], "the asked-for update told, from no release before")
        self.release("0.2.0", ("Asked for.",))
        self.round()
        self.assertEqual([t[0] for t in self.told], ["🆕", "✅", "🆕"], "notify rounds: only the 🆕 notices")
        with self.notify, redirect_stdout(io.StringIO()):
            update.main(["--release"])
        self.assertEqual(len(self.notices()), 2, self.told)
        self.assertTrue(self.notices()[1].startswith("✅ 引擎已更新到 v0.2.0"), self.notices())
        self.assertIn("- Asked for.", self.notices()[1])
        self.assertEqual(self.record()["tag"], "v0.2.0")
        with self.notify, redirect_stdout(io.StringIO()):
            update.main(["--release"])
        self.assertEqual(len(self.notices()), 2, "nothing new: told once")

    def test_a_record_and_an_asked_for_release_tell_from_the_record(self):
        self.mode("release")
        self.release("0.1.0")
        self.round()
        self.mode("notify")
        self.release("0.2.0", ("Two.",))
        self.release("0.3.0", ("Three.",))
        with self.notify, redirect_stdout(io.StringIO()):
            update.main(["--release"])
        self.assertEqual(len(self.notices()), 1, self.told)
        self.assertTrue(self.notices()[0].startswith("✅ 引擎已更新到 v0.3.0（中間的 v0.2.0 也一起裝上了）"), self.notices())

    def test_an_old_record_and_an_asked_for_release_tell_from_what_ran_before(self):
        """#code-review on 288359a: a notify machine's record stays at v0.1.0 while v0.2.0 comes by another path
        (apply, home-pull); the asked-for v0.3.0 is told from v0.2.0, not as bringing v0.2.0 too."""
        self.mode("release")
        self.release("0.1.0")
        self.round()
        self.mode("notify")
        self.release("0.2.0", ("Two.",))
        git(self.engine, "fetch", "-q", "--tags", "origin")
        git(self.engine, "merge", "-q", "--ff-only", "v0.2.0")
        self.round()
        self.assertEqual(self.notices(), [], "notify rounds tell no ✅")
        self.assertEqual(self.record()["tag"], "v0.1.0", "and keep the record where it was")
        self.release("0.3.0", ("Three.",))
        with self.notify, redirect_stdout(io.StringIO()):
            update.main(["--release"])
        self.assertEqual(len(self.notices()), 1, self.told)
        n = self.notices()[0]
        self.assertTrue(n.startswith("✅ 引擎已更新到 v0.3.0"), n)
        self.assertNotIn("v0.2.0", n, "v0.2.0 ran before this update")
        self.assertNotIn("Two.", n)
        self.assertIn("- Three.", n)
        self.assertEqual(self.record()["tag"], "v0.3.0")

    def changelog(self, versions):
        """A CHANGELOG with a section for each of `versions` (newest first), as the real one keeps them all."""
        return "# Changelog\n\n" + "".join("## v%s — 2026-10-20\n\n### For people\n\n%s\n\n### For agents\n\n- Agents only.\n\n" % (
            v, "\n".join("- %s" % p for p in pts)) for v, pts in versions)

    def cumulative(self, versions):
        """Tag each of `versions` ((version, points), oldest first; the ones tagged already left) on a commit whose
        CHANGELOG has them all so far."""
        for i, (v, _) in enumerate(versions):
            if git(self.up, "tag", "-l", "v" + v).stdout.strip():
                continue  # tagged by an earlier call
            self.write(self.up, "CHANGELOG.md", self.changelog(list(reversed(versions[:i + 1]))))
            self.publish("tool.py", "X = %r\n" % v)
            git(self.up, "tag", "v" + v)
            git(self.up, "push", "-q", "origin", "v" + v)

    def test_several_at_once_list_each_newest_first(self):
        """C-57 (owner 2026-10-19): what each release between brought, newest first, EACH_LINES points each."""
        self.mode("release")
        self.cumulative([("0.1.0", ["Start."])])
        self.round()
        self.cumulative([("0.1.0", ["Start."]), ("0.2.0", ["Two a.", "Two b.", "Two c.", "Two d."]), ("0.3.0", ["Three."])])
        self.round()
        n = self.notices()[0]
        self.assertTrue(n.startswith("✅ 引擎已更新到 v0.3.0（中間的 v0.2.0 也一起裝上了）"), n)
        self.assertLess(n.index("**v0.3.0**"), n.index("**v0.2.0**"), "newest first")
        self.assertIn("- Three.", n)
        self.assertIn("- Two c.", n)
        self.assertNotIn("Two d.", n, "EACH_LINES of each")
        self.assertNotIn("Start.", n, "only what is new here")
        self.assertIn("完整的在 CHANGELOG.md", n)

    def test_many_releases_name_the_ones_that_do_not_fit(self):
        self.mode("release")
        self.cumulative([("0.1.0", ["Start."])])
        self.round()
        many = [("0.1.0", ["Start."])] + [("0.%d.0" % i, ["Point %d %s." % (i, "x" * 200)] * 3) for i in range(2, 9)]
        self.cumulative(many)
        self.round()
        n = self.notices()[0]
        self.assertLess(len(n), update.ALL_CHARS + 600, len(n))
        self.assertIn("**v0.8.0**", n)
        self.assertRegex(n, r"還有 v0\.\d\.0(、v0\.\d\.0)* 的改動沒列出來")
        self.assertIn("v0.2.0 的改動沒列出來", n, "the oldest ones are the ones left out")

    def own_words(self, version, people, zh, agents="- Agents only.\n"):
        """A CHANGELOG section with English points and, when `zh` is not None, a "For people (zh-TW)" part."""
        text = "## v%s — 2026-10-20\n\n### For people\n\n%s\n\n" % (version, "\n".join("- " + p for p in people))
        if zh is not None:
            text += "### For people (zh-TW)\n\n%s\n\n" % "\n".join("- " + p for p in zh)
        return text + "### For agents\n\n" + agents + "\n"

    def test_the_installations_own_words_are_quoted_else_english(self):
        """C-57 U8: core.language (zh-TW when not set) picks "For people (zh-TW)"; none, or one without a point, or
        another language: the English For people. Never the For agents part."""
        log = "# Changelog\n\n" + self.own_words("0.3.0", ["**English lead.** More."], ["**中文的重點。** 細節。"]) \
            + self.own_words("0.2.0", ["Only English. More."], None) + self.own_words("0.1.0", ["Old one."], [])
        notes = lambda tag, **kw: update.release_notes(None, tag, log, **kw)
        self.assertEqual(notes("v0.3.0"), (["**中文的重點。**"], True), "not set: zh-TW")
        self.assertEqual(notes("v0.3.0", lang="ZH-tw"), (["**中文的重點。**"], True))
        self.assertEqual(notes("v0.3.0", lang="en"), (["**English lead.**"], True))
        self.assertEqual(notes("v0.2.0"), (["Only English."], True), "a release without them: English")
        self.assertEqual(notes("v0.1.0"), (["Old one."], False), "a part without a point: English")
        for core, lang in (({"language": "en"}, "en"), ({"language": " zh-TW "}, "zh-TW"), ({}, "zh-TW"),
                           ({"language": 3}, "zh-TW"), ({"language": ""}, "zh-TW")):
            with open(os.path.join(self.inst, "plugins.json"), "w") as f:
                json.dump({"format": 1, "core": core}, f)
            self.assertEqual(update.notes_language(), lang, core)
        self.assertEqual(notes("v0.3.0"), (["**中文的重點。**"], True))
        with open(os.path.join(self.inst, "plugins.json"), "w") as f:
            f.write("{ broken")
        self.assertEqual(update.notes_language(), "zh-TW", "settings that cannot be read: the default")

    def test_a_chinese_first_sentence_ends_at_its_full_stop(self):
        """No bold lead: in the installation's words a sentence ends at 。！？ with no space after it; the English
        part keeps ending only where a space follows (its quoted 「…。」 is not the end)."""
        log = "# Changelog\n\n" + self.own_words(
            "0.3.0", ["Say 「引擎回到更新前。」 to go back. More."], ["第一句沒有粗體。第二句", "問號也算？後面", "沒有句號的一項"])
        self.assertEqual(update.release_notes(None, "v0.3.0", log), (["第一句沒有粗體。", "問號也算？", "沒有句號的一項"], True))
        self.assertEqual(update.release_notes(None, "v0.3.0", log, lang="en"),
                         (["Say 「引擎回到更新前。」 to go back."], True))

    def test_several_at_once_each_in_its_own_words_or_english(self):
        """The notice after an update over several releases: each release by the same rule, the ones without the
        installation's words in English (v0.2.x have none)."""
        self.mode("release")
        self.cumulative([("0.1.0", ["Start."])])
        self.round()
        log = "# Changelog\n\n" + self.own_words("0.3.0", ["**Three.**"], ["**三。**"]) \
            + self.own_words("0.2.0", ["**Two.**"], None) + self.own_words("0.1.0", ["Start."], None)
        for v in ("0.2.0", "0.3.0"):
            self.write(self.up, "CHANGELOG.md", log)
            self.publish("tool.py", "X = %r\n" % v)
            git(self.up, "tag", "v" + v)
            git(self.up, "push", "-q", "origin", "v" + v)
        self.round()
        n = self.notices()[0]
        self.assertTrue(n.startswith("✅ 引擎已更新到 v0.3.0（中間的 v0.2.0 也一起裝上了）"), n)
        self.assertIn("**v0.3.0**\n- **三。**", n)
        self.assertIn("**v0.2.0**\n- **Two.**", n)
        self.assertNotIn("**Three.**", n)
        self.assertNotIn("Agents only", n)

    def test_the_new_release_notice_lists_what_is_new_since_this_machine(self):
        self.mode("release")
        self.cumulative([("0.1.0", ["Start."])])
        self.round()
        self.mode("notify")
        self.cumulative([("0.1.0", ["Start."]), ("0.2.0", ["Two."]), ("0.3.0", ["Three."])])
        self.round()
        new = [t for t in self.told if t.startswith("🆕")]
        self.assertEqual(len(new), 1, self.told)
        self.assertTrue(new[0].startswith("🆕 有新版本 v0.3.0 可以更新（這台現在是 v0.1.0）"), new[0])
        self.assertLess(new[0].index("**v0.3.0**"), new[0].index("**v0.2.0**"))
        self.assertNotIn("Start.", new[0])
        self.assertIn("更新引擎", new[0])
        for word in ("commit", "rebase", "worktree", "HEAD", "core.update"):
            self.assertNotIn(word, new[0], "plain words (C-57)")

    def test_notices_go_to_the_update_channel(self):
        self.mode("release")
        self.release("0.1.0")
        self.round()
        self.release("0.2.0")
        self.round()
        self.assertEqual(self.where, ["update"], "C-57: #update when not set")
        with open(os.path.join(self.inst, "plugins.json"), "w") as f:
            json.dump({"format": 1, "core": {"update": "release", "update_channel": "系統/更新"}}, f)
        self.release("0.3.0")
        self.round()
        self.assertEqual(self.where[-1], "系統/更新")
        for broken in ('{"core": {"update_channel": 3}}', "{ broken"):
            with open(os.path.join(self.inst, "plugins.json"), "w") as f:
                f.write(broken)
            self.assertEqual(update.update_channel(), "update", broken)

    def test_a_record_someone_broke_is_a_new_start(self):
        self.mode("release")
        self.release("0.1.0")
        os.makedirs(self.state, exist_ok=True)
        for broken in ("{not json", "[1]", json.dumps({"tag": "latest"})):
            with open(os.path.join(self.state, "updated-release"), "w") as f:
                f.write(broken)
            self.round()
            self.assertEqual(self.notices(), [], broken)
            self.assertEqual(self.record()["tag"], "v0.1.0")

    def test_long_notes_are_cut_and_point_to_the_changelog(self):
        self.mode("release")
        self.release("0.1.0")
        self.round()
        self.release("0.2.0", ["Point %d is here" % i + "x" * 300 for i in range(10)])
        self.round()
        n = self.notices()[0]
        self.assertLessEqual(n.count("\n- "), update.NOTES_LINES)
        self.assertLess(len(n), update.NOTES_CHARS + 300)
        self.assertIn("CHANGELOG.md 的 v0.2.0 一節", n)

    def test_a_release_without_a_changelog_section_says_so(self):
        self.mode("release")
        self.release("0.1.0")
        self.round()
        self.publish("CHANGELOG.md", "# Changelog\n")
        git(self.up, "tag", "v0.2.0")
        git(self.up, "push", "-q", "origin", "v0.2.0")
        self.round()
        self.assertIn("CHANGELOG.md 裡沒有這版的說明", self.notices()[0])


class ReleaseRig(Rig):
    """A machine with an instance whose core.update the test sets, the real supervisor, v0.1.0 published."""

    mode, tag = Modes.mode, Modes.tag

    def setUp(self):
        super().setUp()
        self.inst = os.path.join(self.tmp, "實例 instance")
        os.makedirs(self.inst)
        self.env = mock.patch.dict(os.environ, {"ALT_INSTANCE": self.inst})
        self.env.start()
        os.makedirs(os.path.join(self.up, "dcbot"))
        shutil.copy(os.path.join(ENGINE_REAL, "dcbot", "supervisor.py"), os.path.join(self.up, "dcbot", "supervisor.py"))
        self.good = self.publish("dcbot/.gitignore", "state/\n")
        self.tag("v0.1.0")
        git(self.engine, "pull", "-q", "--ff-only")
        os.makedirs(os.path.join(self.engine, "dcbot", "state"))

    def tearDown(self):
        self.env.stop()
        super().tearDown()


class ReleaseRollbacks(ReleaseRig):
    """C-07: to a newer release, the rollback commits the supervisor made here are left behind (not put on top of
    it), only when every local commit is one (manager 2026-10-17: its subject, this repo's author, on no remote);
    a rollback mixed with anything else stops the round and says so. The rollback is made by the real supervisor."""

    def rolled_back(self, mode):
        """v0.2.0 brought a bad altention/; this machine took it, then its supervisor rolled it back."""
        self.mode(mode)
        self.bad = self.publish("altention/x.py", "BAD = 1\n")
        self.tag("v0.2.0")
        git(self.engine, "fetch", "-q", "--tags")
        git(self.engine, "merge", "-q", "--ff-only", "v0.2.0")
        import importlib.util
        spec = importlib.util.spec_from_file_location("sup_r", os.path.join(self.engine, "dcbot", "supervisor.py"))
        sup = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(sup)
        sup.LOCK_WAIT = 1
        with redirect_stdout(io.StringIO()):
            self.assertTrue(sup.rollback(self.good, "新版本 `%s` 啟動失敗（exit 1）" % self.bad[:7]))
        self.assertFalse(os.path.exists(os.path.join(self.engine, "altention", "x.py")))
        return self.head()

    def fixed(self):
        git(self.up, "rm", "-q", "altention/x.py")
        git(self.up, "commit", "-qm", "the fix")
        git(self.up, "push", "-q")
        self.tag("v0.3.0")
        return git(self.up, "rev-parse", "HEAD").stdout.strip()

    def main(self, *argv):
        out = io.StringIO()
        with self.notify, redirect_stdout(out):
            code = update.main(list(argv))
        return code, out.getvalue()

    def test_the_next_release_leaves_the_rollback_behind(self):
        for mode, argv in (("release", ()), ("notify", ("--release",))):
            with self.subTest(mode=mode):
                back = self.rolled_back(mode)
                code, out = self.main(*argv)
                self.assertEqual((code, self.head()), (0, back), "nothing newer than v0.2.0 yet: the rollback stays")
                fix = self.fixed()
                code, out = self.main(*argv)
                self.assertEqual((code, self.head()), (0, fix), "exactly the release: the rollback not put on it\n" + out)
                self.assertIn("退回 commit（%s）沒有帶到新版上" % back[:7], out)
                self.tearDown()
                self.setUp()

    def test_a_rollback_with_a_commit_of_the_owners_stops_and_says_so(self):
        back = self.rolled_back("release")
        self.write(self.engine, "mine.txt", "local\n")
        git(self.engine, "add", "mine.txt")
        git(self.engine, "commit", "-qm", "my own change")
        mine = self.head()
        self.fixed()
        code, out = self.main()
        self.assertEqual((code, self.head()), (1, mine), out)
        self.assertIn("不猜哪些要留", self.alerts())
        self.assertIn("%s（退回）" % back[:7], self.alerts())
        self.assertTrue(os.path.exists(os.path.join(self.engine, "mine.txt")))

    def looks_like_one_but(self, how):
        back = self.rolled_back("release")
        if how == "another author":
            git(self.engine, "reset", "-q", "--hard", "HEAD~1")
            subject = git(self.engine, "log", "-1", "--format=%s", back).stdout.strip()
            git(self.engine, "rm", "-q", "altention/x.py")
            other = dict(os.environ, GIT_AUTHOR_NAME="o", GIT_AUTHOR_EMAIL="other@example.com")  # over any set before
            subprocess.run(["git", "-C", self.engine, "commit", "-qm", subject], check=True, stdout=subprocess.PIPE, env=other)
            self.assertEqual(git(self.engine, "log", "-1", "--format=%ae").stdout.strip(), "other@example.com")
        else:
            git(self.engine, "push", "-q", "origin", "HEAD:refs/heads/side")
            git(self.engine, "fetch", "-q")
        back = self.head()
        self.fixed()
        code, out = self.main()
        self.assertEqual((code, self.head()), (1, back), out)
        self.assertIn("%s（退回）" % back[:7], self.alerts())

    def test_a_rollback_that_carries_a_file_of_the_owners_stops(self):
        """#code-review of 239abcc: the author is this repo's for the owner's commits too, so only what the commit
        does tells it apart. An amended rollback with a file of theirs, a commit of theirs with that subject, a
        subject naming a commit git does not know: none is left behind."""
        for how in ("amended", "by hand", "unknown good"):
            with self.subTest(how=how):
                back = self.rolled_back("release")
                subject = git(self.engine, "log", "-1", "--format=%s", back).stdout.strip()
                if how == "amended":
                    self.write(self.engine, "mine.txt", "local\n")
                    git(self.engine, "add", "mine.txt")
                    git(self.engine, "commit", "-q", "--amend", "--no-edit")
                elif how == "by hand":
                    git(self.engine, "reset", "-q", "--hard", "HEAD~1")
                    git(self.engine, "rm", "-q", "altention/x.py")
                    self.write(self.engine, "mine.txt", "local\n")
                    git(self.engine, "add", "mine.txt")
                    git(self.engine, "commit", "-qm", subject)
                else:
                    git(self.engine, "commit", "-q", "--amend", "-m", subject.replace(self.good[:7], "0000000"))
                self.assertTrue(sync.ROLLBACK_SUBJECT.match(git(self.engine, "log", "-1", "--format=%s").stdout.strip()))
                back = self.head()
                self.fixed()
                code, out = self.main()
                self.assertEqual((code, self.head()), (1, back), out)
                self.assertIn("%s（退回）" % back[:7], self.alerts())
                if how != "unknown good":
                    self.assertTrue(os.path.exists(os.path.join(self.engine, "mine.txt")), "theirs still here")
                self.tearDown()
                self.setUp()

    def test_a_rollback_looking_commit_by_another_author_stops(self):
        self.looks_like_one_but("another author")

    def test_a_rollback_looking_commit_on_a_remote_branch_stops(self):
        self.looks_like_one_but("on a remote branch")

    def test_the_sync_counts_the_rollback_kept_here_for_doctor(self):
        """#bot-dev on fd23d9e: doctor's sync.engine-ahead told a machine on releases to push the rollback its notice
        says to keep. The sync's engine record says how many of the commits ahead are rollbacks (local_commits)."""
        for mode, mine in (("release", False), ("notify", False), ("release", True), ("branch", False)):
            with self.subTest(mode=mode, mine=mine):
                self.rolled_back(mode if mode != "branch" else "release")
                self.mode(mode)
                if mine:
                    self.write(self.engine, "mine.txt", "local\n")
                    git(self.engine, "add", "mine.txt")
                    git(self.engine, "commit", "-qm", "my own change")
                with mock.patch.object(sync.config, "ENGINE", self.engine), \
                        mock.patch.object(sync.config, "instance_dir", return_value=None), \
                        mock.patch.object(update.subprocess, "run", self.fake_run):
                    st = sync.run()
                eng = st["engine"]
                if mode == "branch":
                    self.assertNotIn("rollbacks", eng, "branch: pushed like any commit, nothing told apart")
                else:
                    self.assertEqual((eng["ahead"], eng["rollbacks"], eng["rollbacks_unsure"]), (2 if mine else 1, 1, 0), eng)
                self.tearDown()
                self.setUp()

    def test_the_sync_counts_an_amended_rollback_as_unsure(self):
        """#code-review-2 of 7daa3df: a rollback amended with a file of the owner's is no rollback (restores() fails),
        and doctor then told how to push it. The sync counts it apart: rollbacks_unsure."""
        self.rolled_back("release")
        self.write(self.engine, "mine.txt", "local\n")
        git(self.engine, "add", "mine.txt")
        git(self.engine, "commit", "-q", "--amend", "--no-edit")
        with mock.patch.object(sync.config, "ENGINE", self.engine), \
                mock.patch.object(sync.config, "instance_dir", return_value=None), \
                mock.patch.object(update.subprocess, "run", self.fake_run):
            st = sync.run()
        eng = st["engine"]
        self.assertEqual((eng["ahead"], eng["rollbacks"], eng["rollbacks_unsure"]), (1, 0, 1), eng)

    def test_home_pull_leaves_it_behind_too(self):
        """bin/home-pull in release mode goes through the same sync.update(to=the release)."""
        self.rolled_back("release")
        fix = self.fixed()
        git(self.engine, "fetch", "-q", "--tags")
        repo = sync.Repo(self.engine)
        with sync.locked(repo):
            code, note = sync.update(repo, to=fix)
        self.assertEqual((code, self.head()), (0, fix), note)
        self.assertIn("沒有帶到新版上", note)


class StepBacks(ReleaseRig):
    """C-44: a step back the supervisor made while a new version's packages would not install is left behind in
    every mode once the upstream moves (branch included: it is never to push), only when every local commit is one;
    mixed with anything else the round stops. The step back is made by the real supervisor."""

    fixed, main = ReleaseRollbacks.fixed, ReleaseRollbacks.main

    def stepped(self, mode):
        """v0.2.0 needs packages that would not install; this machine took it, then its supervisor stepped back."""
        self.mode(mode)
        self.bad = self.publish("altention/x.py", "NEEDS = 1\n")
        self.tag("v0.2.0")
        git(self.engine, "fetch", "-q", "--tags")
        git(self.engine, "merge", "-q", "--ff-only", "v0.2.0")
        import importlib.util
        spec = importlib.util.spec_from_file_location("sup_s", os.path.join(self.engine, "dcbot", "supervisor.py"))
        sup = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(sup)
        sup.LOCK_WAIT = 1
        with redirect_stdout(io.StringIO()):
            self.assertTrue(sup.rollback(self.good, "新版本 `%s` 要的套件一直裝不上（pip …）" % self.bad[:7], step=self.bad))
        subject = git(self.engine, "log", "-1", "--format=%s").stdout.strip()
        self.assertTrue(sync.STEP_BACK_SUBJECT.match(subject) and update.STEP_BACK_SUBJECT.match(subject), subject)
        self.assertEqual(sync.STEP_BACK_SUBJECT.pattern, update.STEP_BACK_SUBJECT.pattern, "the copy in update.py")
        return self.head()

    def test_every_mode_leaves_it_behind_once_the_upstream_moves_and_keeps_it_until_then(self):
        for mode, argv in (("branch", ()), ("release", ()), ("notify", ("--release",))):
            with self.subTest(mode=mode):
                step = self.stepped(mode)
                code, out = self.main(*argv)
                self.assertEqual((code, self.head()), (0, step), "the upstream not moved: the old version runs on\n" + out)
                fix = self.fixed()
                code, out = self.main(*argv)
                self.assertEqual((code, self.head()), (0, fix), "exactly the upstream: the step back not put on it\n" + out)
                self.assertIn("退路 commit（%s" % step[:7], out)
                self.tearDown()
                self.setUp()

    def test_a_rollback_in_branch_mode_is_still_put_on_top(self):
        """A rollback (a version that is broken) is one to push in branch mode: rebased as before, not dropped (its push
        failed, so it is still here)."""
        git(self.engine, "remote", "set-url", "--push", "origin", os.path.join(self.tmp, "nowhere.git"))
        back = ReleaseRollbacks.rolled_back(self, "branch")
        subject = git(self.engine, "log", "-1", "--format=%s", back).stdout.strip()
        fix = self.publish("tool.py", "X = 9\n", "something else upstream")
        code, out = self.main()
        self.assertEqual(code, 0, out)
        self.assertEqual(git(self.engine, "rev-parse", "HEAD^").stdout.strip(), fix)
        self.assertEqual(git(self.engine, "log", "-1", "--format=%s").stdout.strip(), subject)

    def test_a_step_back_with_a_commit_of_the_owners_stops_and_says_so(self):
        for mode in ("branch", "release"):
            with self.subTest(mode=mode):
                step = self.stepped(mode)
                self.write(self.engine, "mine.txt", "local\n")
                git(self.engine, "add", "mine.txt")
                git(self.engine, "commit", "-qm", "my own change")
                mine = self.head()
                self.fixed()
                code, out = self.main()
                self.assertEqual((code, self.head()), (1, mine), out)
                self.assertIn("不猜哪些要留", self.alerts())
                self.assertIn(step[:7] + "（退路）", self.alerts(), "a step back named as one, either mode")
                self.assertTrue(os.path.exists(os.path.join(self.engine, "mine.txt")))
                self.tearDown()
                self.setUp()

    def test_its_subject_alone_by_another_author_or_on_a_remote_branch_is_no_step_back(self):
        for how in ("another author", "on a remote branch"):
            with self.subTest(how=how):
                step = self.stepped("release")
                if how == "another author":
                    subject = git(self.engine, "log", "-1", "--format=%s", step).stdout.strip()
                    git(self.engine, "reset", "-q", "--hard", "HEAD~1")
                    git(self.engine, "rm", "-q", "altention/x.py")
                    other = dict(os.environ, GIT_AUTHOR_NAME="o", GIT_AUTHOR_EMAIL="other@example.com")
                    subprocess.run(["git", "-C", self.engine, "commit", "-qm", subject], check=True, stdout=subprocess.PIPE, env=other)
                else:
                    git(self.engine, "push", "-q", "origin", "HEAD:refs/heads/side")
                    git(self.engine, "fetch", "-q")
                step = self.head()
                backs, steps, others = sync.local_commits(sync.Repo(self.engine), self.bad)
                self.assertEqual((backs, steps, others), ([], [], [(step, True)]))
                self.fixed()
                code, out = self.main()
                self.assertEqual((code, self.head()), (1, step), "on releases: stops rather than guess\n" + out)
                self.tearDown()
                self.setUp()

    def test_home_pull_and_the_sync_standing_in_leave_it_behind_too(self):
        for path in ("home-pull", "sync standing in"):
            with self.subTest(path=path):
                step = self.stepped("branch")
                fix = self.fixed()
                if path == "home-pull":  # bin/home-pull: sync.update on the branch, under the lock
                    repo = sync.Repo(self.engine)
                    with sync.locked(repo):
                        code, note = sync.update(repo)
                    self.assertIn("退路 commit（%s" % step[:7], note)
                    self.assertEqual(code, 0)
                else:
                    os.makedirs(update.state_dir(), exist_ok=True)
                    with open(os.path.join(update.state_dir(), "last_ok-update"), "w") as f:
                        json.dump({"at": time.time() - 2 * 3600}, f)  # update.py stopped two hours ago
                    with mock.patch.object(sync.config, "ENGINE", self.engine), \
                            mock.patch.object(sync.config, "instance_dir", return_value=None), \
                            mock.patch.object(update.subprocess, "run", self.fake_run):
                        st = sync.run()
                    self.assertEqual(st["engine_pulled_by"], "sync")
                self.assertEqual(self.head(), fix)
                self.tearDown()
                self.setUp()

    def test_the_sync_counts_the_step_back_in_every_mode_for_doctor(self):
        for mode in ("branch", "release", "notify"):
            with self.subTest(mode=mode):
                self.stepped(mode)
                with mock.patch.object(sync.config, "ENGINE", self.engine), \
                        mock.patch.object(sync.config, "instance_dir", return_value=None), \
                        mock.patch.object(update.subprocess, "run", self.fake_run):
                    eng = sync.run()["engine"]
                self.assertEqual((eng["ahead"], eng["steps"]), (1, 1), eng)
                if mode != "branch":
                    self.assertEqual((eng["rollbacks"], eng["rollbacks_unsure"]), (0, 0), eng)
                self.tearDown()
                self.setUp()


class NoModeSeen(Rig):
    """C-04: no instance seen and no mode read before: notify, said in the alerts file and told once (the next rounds
    that work do not tell it again every 15 minutes)."""

    def test_said_once(self):
        os.remove(os.path.join(self.state, "update-mode"))
        with mock.patch.dict(os.environ, {"ALT_INSTANCE": "", "ALT_ROOT": os.path.join(self.tmp, "沒有")}):
            old = self.head()
            self.publish("tool.py", "X = 2\n")
            for _ in range(3):
                with self.notify, redirect_stdout(io.StringIO()):
                    self.assertEqual(update.main([]), 0)
            self.assertEqual(self.head(), old, "notify: nothing taken")
            self.assertIn("update\tmode\t", self.alerts())
            self.assertIn("照 notify，不自動更新", self.alerts())
            self.assertEqual(len(self.told), 1, self.told)
            self.assertFalse(os.path.exists(os.path.join(self.state, "update-mode")), "nothing remembered from not seeing it")


class BotRestart(Rig):
    """A move that changes the bot's version (dcbot/ or altention/) writes deploy.sh's restart flag while this
    engine's supervisor runs here, so the bot does not keep running the old code it has in memory (owner's
    re-install, 2026-10-15); the supervisor's trial and rollback do the rest."""

    def setUp(self):
        super().setUp()
        self.flag = os.path.join(self.engine, "dcbot", "state", "restart-requested")
        self.procs = []

    def tearDown(self):
        for p in self.procs:
            p.kill()
            p.wait()
        super().tearDown()

    def supervisor(self, word="supervisor"):
        """A live process whose command line has `word` in it, named in the supervisor's pid file."""
        p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)", word])
        self.procs.append(p)
        os.makedirs(os.path.dirname(self.flag), exist_ok=True)
        with open(os.path.join(os.path.dirname(self.flag), "supervisor.pid"), "w") as f:
            f.write(str(p.pid))

    def main(self, *argv):
        out = io.StringIO()
        with self.notify, redirect_stdout(out):
            code = update.main(list(argv))
        return code, out.getvalue()

    def test_the_bots_code_moved_and_the_bot_runs_here_the_flag_is_written(self):
        self.supervisor()
        new = self.publish("altention/x.py", "Y = 1\n")
        code, out = self.main()
        self.assertEqual((code, self.head()), (0, new))
        with open(self.flag) as f:
            self.assertEqual(json.load(f), {"channel": ""}, "as deploy.sh writes it: #audit only")
        self.assertIn("機器人會自己重啟", out)
        self.assertEqual(self.alerts(), "")

    def test_a_move_outside_dcbot_and_altention_leaves_the_bot_alone(self):
        self.supervisor()
        self.publish("tool.py", "X = 2\n")
        code, out = self.main()
        self.assertEqual(code, 0)
        self.assertIn("更新了", out)
        self.assertFalse(os.path.exists(self.flag))
        self.assertNotIn("機器人", out)

    def test_no_bot_on_this_machine_no_flag(self):
        self.publish("altention/x.py", "Y = 2\n")
        self.main()
        self.assertFalse(os.path.exists(self.flag), "no pid file")
        p = subprocess.Popen([sys.executable, "-c", "pass"])
        p.wait()
        os.makedirs(os.path.dirname(self.flag), exist_ok=True)
        with open(os.path.join(os.path.dirname(self.flag), "supervisor.pid"), "w") as f:
            f.write(str(p.pid))  # gone
        self.publish("altention/x.py", "Y = 3\n")
        self.main()
        self.assertFalse(os.path.exists(self.flag), "a pid no longer running")
        if os.path.isdir("/proc"):
            self.supervisor(word="something-else")
            self.publish("altention/x.py", "Y = 4\n")
            self.main()
            self.assertFalse(os.path.exists(self.flag), "a live pid that is not a supervisor")

    def test_nothing_new_writes_nothing(self):
        self.supervisor()
        self.main()
        self.assertFalse(os.path.exists(self.flag))

    def roll_back(self, good):
        """What the supervisor's rollback leaves: a new commit on top putting dcbot/ and altention/ back."""
        git(self.engine, "rm", "-q", "altention/x.py")  # the only file the bad version brought
        git(self.engine, "commit", "-qm", "dcbot: roll back to %s" % good[:7])
        os.remove(self.flag)  # the bot read it when it came up

    def test_after_a_rollback_the_next_rounds_do_not_take_the_bad_version_again(self):
        for mode in ("release", "branch"):
            with self.subTest(mode=mode):
                self.tearDown()
                self.setUp()
                branch_mode(os.path.expanduser("~"), mode)
                self.supervisor()
                good = self.head()
                bad = self.publish("altention/x.py", "BAD = 1\n")
                if mode == "release":
                    git(self.up, "tag", "v0.1.0")
                    git(self.up, "push", "-q", "origin", "v0.1.0")
                self.main()
                self.assertEqual(self.head(), bad)
                self.roll_back(good)
                back = self.head()
                for _ in range(2):
                    code, out = self.main()
                    self.assertEqual((code, self.head()), (0, back), out)
                    self.assertFalse(os.path.exists(self.flag), "no restart into the bad version")
                self.assertFalse(os.path.exists(os.path.join(self.engine, "altention", "x.py")))

    def test_the_same_paths_as_the_supervisors_version(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("sup", os.path.join(ENGINE_REAL, "dcbot", "supervisor.py"))
        sup = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(sup)
        self.assertEqual(sorted(os.path.normpath(os.path.join("dcbot", p)) for p in sup.PATHS),
                         sorted(update.BOT_PATHS))


class Deps(Rig):
    """C-08: a move that changes dcbot/requirements.txt installs the bot's packages (pip into its venv, as
    dcbot/install.sh) before the restart flag; pip failing writes no flag (the bot keeps the old code, which has its
    packages), deps-pending for doctor, told once, retried each round until it works. C-09: the other paths that move
    the engine (the launcher's plain fast-forward, the sync standing in, home-pull) write the flag too."""
    supervisor, main = BotRestart.supervisor, BotRestart.main
    BREAK = BrokenSomewhere.BREAK

    def setUp(self):
        Rig.setUp(self)
        self.flag = os.path.join(self.engine, "dcbot", "state", "restart-requested")
        self.procs = []
        self.write(self.up, "dcbot/requirements.txt", "discord.py\n")
        self.publish("dcbot/bot.py", "B = 1\n")
        git(self.engine, "pull", "-q", "--ff-only")
        venv = os.path.join(self.engine, "dcbot", ".venv", "bin")
        os.makedirs(venv)
        self.pip_log = os.path.join(self.tmp, "pip.log")
        self.pip_fails = os.path.join(self.tmp, "pip-fails")
        self.pip_sleeps = os.path.join(self.tmp, "pip-sleeps")
        with open(os.path.join(venv, "python"), "w") as f:  # the venv's python: only `-m pip install`, logged
            # it also writes down whether the engine's git lock was free while it ran, and sleeps if pip-sleeps says
            f.write('#!/bin/sh\necho "$PWD|$*" >> "%s"\n'
                    '/usr/bin/python3 -c "import fcntl,sys; fcntl.flock(open(sys.argv[1], \'a\'), fcntl.LOCK_EX | fcntl.LOCK_NB)" '
                    '"%s" 2>/dev/null && echo free >> "%s.lock" || echo held >> "%s.lock"\n'
                    '[ -e "%s" ] && sleep "$(cat "%s")"\n'
                    '[ -e "%s" ] && { echo "no network" ; exit 1; }\nexit 0\n'
                    % (self.pip_log, os.path.join(self.engine, ".git", "altention-git.lock"), self.pip_log, self.pip_log,
                       self.pip_sleeps, self.pip_sleeps, self.pip_fails))
        os.chmod(os.path.join(venv, "python"), 0o755)

    def tearDown(self):
        for p in self.procs:
            p.kill()
            p.wait()
        Rig.tearDown(self)

    def pips(self):
        try:
            with open(self.pip_log) as f:
                return [l.split("|", 1)[1].strip() for l in f.read().splitlines()]
        except OSError:
            return []

    def pending(self):
        return update.deps_pending()

    def test_new_packages_come_before_the_restart(self):
        self.supervisor()
        new = self.publish("dcbot/requirements.txt", "discord.py\nnewthing\n")
        code, out = self.main()
        self.assertEqual((code, self.head()), (0, new))
        self.assertEqual(self.pips(), ["-m pip install -q -r requirements.txt"])
        with open(self.pip_log) as f:
            self.assertTrue(f.read().startswith(os.path.join(self.engine, "dcbot") + "|"), "run in dcbot/, as install.sh")
        self.assertTrue(os.path.exists(self.flag))
        self.assertIsNone(self.pending())
        self.assertIn("機器人會自己重啟", out)

    def test_pip_failing_writes_no_flag_says_so_once_and_is_tried_again(self):
        self.supervisor()
        open(self.pip_fails, "w").close()
        new = self.publish("dcbot/requirements.txt", "discord.py\nnewthing\n")
        code, out = self.main()
        self.assertEqual((code, self.head()), (0, new), "HEAD moved on; the bot did not")
        self.assertFalse(os.path.exists(self.flag), "no restart into code whose packages are missing")
        self.assertEqual(self.pending()["head"], new)
        self.assertIn("no network", self.pending()["why"])
        self.assertIn("update\tdeps\t", self.alerts())
        self.assertIn("dcbot/install.sh", self.alerts())
        self.assertIn("套件沒裝上，沒有重啟", out)
        self.assertEqual(len(self.told), 1)
        for _ in range(2):  # within the hour: not tried again (#code-review of df95bc2: a hung network every round)
            self.main()
        self.assertEqual(len(self.pips()), 1)
        self.an_hour_later()
        self.main()
        self.assertEqual(len(self.pips()), 2, "an hour later: tried again")
        self.assertEqual(len(self.told), 1, "told the first time only: doctor keeps saying it")
        self.assertEqual(self.pending()["tries"], 2)
        self.assertFalse(os.path.exists(self.flag))
        os.remove(self.pip_fails)  # the network back, or install.sh run by hand
        self.an_hour_later()
        code, out = self.main()
        self.assertIsNone(self.pending())
        self.assertTrue(os.path.exists(self.flag), "now the bot restarts into it")
        self.assertIn("套件裝好了，機器人會自己重啟", out)
        with open(self.pip_log + ".lock") as f:
            self.assertEqual(set(f.read().split()), {"free"}, "pip never runs holding the engine's git lock")

    def an_hour_later(self):
        p = self.pending()
        p["next"] = time.time() - 1
        with open(os.path.join(update.state_dir(), update.DEPS_PENDING), "w") as f:
            json.dump(p, f)

    def test_a_pip_killed_halfway_is_taken_up_by_the_next_round(self):
        """#code-review of df95bc2: the round killed (cron_update's 900 s, a Bash 2-minute limit) while pip ran
        left neither the flag nor a record, and nothing ever tried again. The record is there before pip runs."""
        self.supervisor()
        with open(self.pip_sleeps, "w") as f:
            f.write("30")
        new = self.publish("dcbot/requirements.txt", "discord.py\nnewthing\n")
        p = subprocess.Popen([sys.executable, "-c", "import sys; sys.path.insert(0, %r)\nfrom unittest import mock\n"
                              "from altention import update\nupdate.ENGINE = %r\nupdate.main([])" % (ENGINE_REAL, self.engine)],
                             start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        until = time.time() + 30
        while time.time() < until and not self.pips():
            time.sleep(0.2)
        self.assertEqual(len(self.pips()), 1, "pip started")
        os.killpg(p.pid, 9)  # as cron_update does after 900 s
        p.wait()
        self.assertEqual(self.head(), new)
        self.assertFalse(os.path.exists(self.flag))
        self.assertEqual((self.pending()["head"], self.pending()["why"]), (new, update.INSTALLING), "the record is there")
        rec = self.pending()
        rec["next"] = time.time() - 1  # the round after: past PIP_TIMEOUT (15 minutes later)
        with open(os.path.join(update.state_dir(), update.DEPS_PENDING), "w") as f:
            json.dump(rec, f)
        os.remove(self.pip_sleeps)
        code, out = self.main()
        self.assertEqual(len(self.pips()), 2, "taken up")
        self.assertIsNone(self.pending())
        self.assertTrue(os.path.exists(self.flag))
        self.assertGreater(update.PIP_TIMEOUT + 60, 0)

    def test_head_moving_on_while_pip_runs_keeps_the_record_for_the_new_head(self):
        self.supervisor()
        new = self.publish("dcbot/requirements.txt", "discord.py\nnewthing\n")
        real = update.install_deps
        newer = []

        def moved_meanwhile():
            newer.append(self.publish("dcbot/bot.py", "B = 9\n"))
            git(self.engine, "pull", "-q", "--ff-only")  # say, home-pull in another channel
            return real()
        with mock.patch.object(update, "install_deps", moved_meanwhile):
            code, out = self.main()
        self.assertEqual(self.head(), newer[0])
        self.assertFalse(os.path.exists(self.flag), "not for a HEAD it did not install for")
        self.assertEqual((self.pending()["head"], self.pending()["why"]), (newer[0], update.INSTALLING))
        self.assertIn("引擎又前進了", out)
        code, out = self.main()
        self.assertIsNone(self.pending())
        self.assertTrue(os.path.exists(self.flag))

    def test_a_move_while_packages_are_pending_does_not_restart_into_it(self):
        self.supervisor()
        open(self.pip_fails, "w").close()
        self.publish("dcbot/requirements.txt", "discord.py\nnewthing\n")
        self.main()
        newer = self.publish("dcbot/bot.py", "B = 7\n")  # requirements unchanged since: still missing
        self.main()
        self.assertEqual(self.head(), newer)
        self.assertFalse(os.path.exists(self.flag))
        self.assertEqual(self.pending()["head"], newer)
        self.assertIn("no network", self.pending()["why"], "the reason kept")

    def test_a_step_back_of_the_supervisors_is_left_to_it(self):
        """C-44: HEAD is the supervisor's step back while the new version's packages install: update.py runs no pip,
        writes no flag and keeps the record (the supervisor alone moves forward)."""
        self.supervisor()
        new = self.publish("dcbot/requirements.txt", "discord.py\nnewthing\n")
        update.mark_deps(new, "pip install 失敗", 0, 1)
        git(self.engine, "commit", "-q", "--allow-empty", "-m", "dcbot: step back to %s while packages install (%s)" % (self.head()[:7], new[:7]))
        self.assertEqual(update.install_pending(), "")
        self.assertEqual((self.pips(), os.path.exists(self.flag)), ([], False))
        self.assertEqual(self.pending()["head"], new)
        git(self.engine, "commit", "-q", "--allow-empty", "-m", "something else")
        update.install_pending()
        self.assertEqual(len(self.pips()), 1, "any other HEAD: as before")

    def test_the_same_requirements_or_no_venv_run_no_pip(self):
        self.supervisor()
        new = self.publish("dcbot/bot.py", "B = 2\n")
        self.main()
        self.assertEqual((self.head(), self.pips()), (new, []))
        self.assertTrue(os.path.exists(self.flag))
        os.remove(self.flag)
        shutil.rmtree(os.path.join(self.engine, "dcbot", ".venv"))  # the bot is not installed on this machine
        self.publish("dcbot/requirements.txt", "other\n")
        self.main()
        self.assertEqual(self.pips(), [])
        self.assertTrue(os.path.exists(self.flag), "the version changed all the same")

    def test_the_launchers_plain_fast_forward_restarts_the_bot_too(self):
        self.supervisor()
        self.publish("altention/update.py", BrokenSomewhere.broken(self, "altention/update.py", "load"))
        git(self.engine, "pull", "-q", "--ff-only")  # update.py itself is what broke here
        new = self.publish("dcbot/bot.py", "B = 3\n")
        r = BrokenSomewhere.launch(self, "cron_update.py")
        self.assertEqual(self.head(), new, r.stdout[-400:])
        self.assertTrue(os.path.exists(self.flag), r.stdout[-400:])
        os.remove(self.flag)
        new = self.publish("dcbot/requirements.txt", "discord.py\nnewthing\n")
        r = BrokenSomewhere.launch(self, "cron_update.py")
        self.assertEqual(self.head(), new, r.stdout[-400:])
        self.assertFalse(os.path.exists(self.flag), "packages not installed by the fallback: no restart")
        self.assertEqual(self.pending()["head"], new)
        self.assertIn("cron-update\tdeps\t", self.alerts())
        self.assertEqual(self.pips(), [])

    def test_the_fallback_does_not_restart_while_packages_are_pending(self):
        """#code-review of 5c3ee2f: packages pending from an earlier move, then the launcher's fast-forward (update.py
        broken) to a version with only dcbot/ changed: it wrote the flag, the bot restarting into code without its
        packages. As update.py's after_move: no flag, the record kept for the new HEAD."""
        self.supervisor()
        open(self.pip_fails, "w").close()
        self.publish("dcbot/requirements.txt", "discord.py\nnewthing\n")
        self.main()
        self.assertIn("no network", self.pending()["why"])
        tries = self.pending()["tries"]
        self.publish("altention/update.py", BrokenSomewhere.broken(self, "altention/update.py", "load"))
        git(self.engine, "pull", "-q", "--ff-only")  # update.py itself is what broke here
        self.assertFalse(os.path.exists(self.flag))
        new = self.publish("dcbot/bot.py", "B = 5\n")
        r = BrokenSomewhere.launch(self, "cron_update.py")
        self.assertEqual(self.head(), new, r.stdout[-400:])
        self.assertFalse(os.path.exists(self.flag), "no restart into code whose packages are missing")
        self.assertEqual(self.pending()["head"], new, "the record kept, for the new HEAD")
        self.assertIn("no network", self.pending()["why"], "with its reason")
        self.assertEqual(self.pending()["tries"], tries)

    def test_the_sync_standing_in_restarts_the_bot_too(self):
        self.supervisor()
        os.makedirs(update.state_dir(), exist_ok=True)
        with open(os.path.join(update.state_dir(), "last_ok-update"), "w") as f:
            json.dump({"at": time.time() - 2 * 3600}, f)  # update.py stopped two hours ago
        new = self.publish("dcbot/requirements.txt", "discord.py\nnewthing\n")
        with mock.patch.object(sync.config, "ENGINE", self.engine), \
                mock.patch.object(sync.config, "instance_dir", return_value=None), \
                mock.patch.object(update.subprocess, "run", self.fake_run):
            sync.run()
        self.assertEqual(self.head(), new)
        self.assertEqual(len(self.pips()), 1)
        self.assertTrue(os.path.exists(self.flag))

    def test_home_pull_restarts_the_bot_too(self):
        import importlib.machinery
        import importlib.util
        loader = importlib.machinery.SourceFileLoader("home_pull_t", os.path.join(ENGINE_REAL, "bin", "home-pull"))
        spec = importlib.util.spec_from_loader("home_pull_t", loader)
        hp = importlib.util.module_from_spec(spec)
        loader.exec_module(hp)
        self.supervisor()
        new = self.publish("dcbot/bot.py", "B = 4\n")
        out = io.StringIO()
        with mock.patch.object(hp, "REPO", self.engine), mock.patch.object(sync.config, "instance_dir", return_value=None), \
                redirect_stdout(out):
            hp.main([])
        self.assertEqual(self.head(), new)
        self.assertTrue(os.path.exists(self.flag))
        self.assertIn("機器人會自己重啟", out.getvalue())


class ModeReading(unittest.TestCase):
    """The five copies of update_mode() (update.py, sync.py, both launchers, the supervisor) give the same answer for
    every case."""

    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="模式 ")
        self.env = mock.patch.dict(os.environ, {"HOME": self.home, "ALT_ROOT": os.path.join(self.home, "root"),
                                                "ALT_INSTANCE": ""})
        self.env.start()
        self.copies = [("update.py", update.update_mode), ("sync.py", sync.update_mode)]
        import importlib.util
        for name in ("cron_update.py", "cron_sync.py"):
            spec = importlib.util.spec_from_file_location("m_" + name[:-3], os.path.join(ENGINE_REAL, "altention", name))
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            self.copies.append((name, mod.update_mode))
        spec = importlib.util.spec_from_file_location("m_supervisor", os.path.join(ENGINE_REAL, "dcbot", "supervisor.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        self.copies.append(("supervisor.py", mod.update_mode))

    def tearDown(self):
        self.env.stop()
        shutil.rmtree(self.home, ignore_errors=True)

    def answers(self):
        got = {name: fn() for name, fn in self.copies}
        self.assertEqual(len(set((m, bool(p)) for m, p in got.values())), 1, got)
        return got["update.py"]

    def settings(self, text, where=None):
        inst = where or os.path.join(self.home, "root", "instance")
        os.makedirs(inst, exist_ok=True)
        with open(os.path.join(inst, "plugins.json"), "w", encoding="utf-8") as f:
            f.write(text)
        return inst

    def test_every_case(self):
        m, problem = self.answers()
        self.assertEqual(m, "notify", "no instance, nothing read before")
        self.assertIn("看不到實例", problem, "and said (C-04)")
        self.assertIn("照 notify，不自動更新", problem)
        branch_mode(self.home)
        self.assertEqual(self.answers(), ("branch", ""), "no instance seen: the mode last read")
        inst = self.settings('{"format": 1}')
        self.assertEqual(self.answers(), ("notify", ""), "read, not set: the default")
        for value in ("notify", "release", "branch"):
            self.settings('{"core": {"update": "%s"}}' % value)
            self.assertEqual(self.answers(), (value, ""))
        for bad in ('{"core": {"update": "nightly"}}', '{"core": {"update": true}}', '{"core": []}', '[]', '{ broken',
                    '\udcff'):
            self.settings(bad.encode("utf-8", "surrogateescape").decode("utf-8", "replace") if bad == '\udcff' else bad)
            m, problem = self.answers()
            self.assertEqual(m, "branch", bad)
            self.assertTrue(problem, bad)
        os.remove(os.path.join(inst, "plugins.json"))
        self.assertEqual(self.answers(), ("branch", ""), "an instance with no settings file: the mode last read")
        with mock.patch.dict(os.environ, {"ALT_INSTANCE": os.path.join(self.home, "不在 這裡")}):
            m, problem = self.answers()
            self.assertEqual(m, "branch")
            self.assertIn("不存在", problem)
        with mock.patch.dict(os.environ, {"ALT_INSTANCE": "-"}):
            self.assertEqual(self.answers(), ("branch", ""))
        other = self.settings('{"core": {"update": "release"}}', os.path.join(self.home, "其他 實例"))
        os.makedirs(os.path.join(self.home, ".config", "altention"))
        with open(os.path.join(self.home, ".config", "altention", "instance"), "w") as f:
            f.write(other + "\n")
        self.assertEqual(self.answers(), ("release", ""), "the pointer names the instance")
        branch_mode(self.home, "weird")
        os.remove(os.path.join(other, "plugins.json"))
        m, problem = self.answers()
        self.assertEqual(m, "notify", "a mode last read that is not one: notify")
        self.assertIn("實例裡沒有", problem)
        with mock.patch.dict(os.environ, {"ALT_INSTANCE": "-"}):
            self.assertIn("這台已經離開實例", self.answers()[1])


class Launchers(unittest.TestCase):
    def load(self, name):
        import importlib.util
        spec = importlib.util.spec_from_file_location("launcher_" + name[:-3], os.path.join(ENGINE_REAL, "altention", name))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def test_anything_unexpected_in_the_launcher_still_reaches_the_fallback(self):
        for name in ("cron_update.py", "cron_sync.py"):
            mod = self.load(name)
            said, ff = [], []
            with mock.patch.object(mod, "round_or_fallback", side_effect=ValueError("unexpected")), \
                    mock.patch.object(mod, "plain_ff", lambda: ff.append(1) or "快轉好了"), \
                    mock.patch.object(mod, "note", said.append):
                self.assertEqual(mod.main(), 1, name)
            self.assertEqual(ff, [1], name)
            self.assertIn("啟動檔自己出錯（ValueError", said[0])

    def test_every_writer_keeps_the_alerts_file_to_200_lines(self):
        folder = update.state_dir()
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, "alerts")
        try:
            for writer in (lambda: self.load("cron_update.py").note("from the launcher"),
                           lambda: self.load("cron_sync.py").note("from the other launcher"),
                           lambda: sync._alert_plain("sync", "x", "from the sync")):
                with open(path, "w") as f:
                    f.writelines("old %d\n" % i for i in range(250))
                writer()
                with open(path) as f:
                    rows = f.readlines()
                self.assertEqual(len(rows), 200)
                self.assertIn("from", rows[-1])
        finally:
            os.remove(path)

    def test_standard_library_only_nothing_of_the_engine(self):
        for name in ("cron_update.py", "cron_sync.py"):
            with open(os.path.join(ENGINE_REAL, "altention", name), encoding="utf-8") as f:
                tree = ast.parse(f.read())
            names = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    names |= {a.name.split(".")[0] for a in node.names}
                elif isinstance(node, ast.ImportFrom):
                    names.add((node.module or "").split(".")[0])
            self.assertEqual(names - STDLIB, set(), name)
            self.assertIn("altention/%s" % name, update.ALWAYS, "checked every time")


class StaysRefused(BrokenSomewhere):
    """#code-review-2 on 61fc0d7, manager 2026-10-06: an upstream that does not pass stays refused; neither the sync
    (however long update.py has been refusing) nor a launcher's fallback brings it in; a fix lets it through."""

    def test_a_day_of_refusing_then_the_fix(self):
        old = self.head()
        bad = self.publish("ops/doctor.py", "def broken(:\n")
        for hour in range(0, 24, 3):  # cron_update and cron_sync in turn, the clock turned on by three hours
            r1 = self.launch("cron_update.py")
            self.assertEqual(self.head(), old, "hour %d: update moved\n%s" % (hour, r1.stdout[-300:]))
            for name in ("last_ok-update", "last_ok-sync"):
                p = os.path.join(self.state, name)
                if os.path.exists(p):
                    with open(p, "w") as f:
                        json.dump({"at": time.time() - 3 * 3600}, f)
            r2 = self.launch("cron_sync.py")
            self.assertEqual(self.head(), old, "hour %d: the sync pulled the refused commit in\n%s" % (hour, r2.stdout[-300:]))
        self.assertEqual(update.blocked_hash(), bad)
        fix = self.publish("ops/doctor.py", "X = 'fixed'\n")
        self.launch("cron_update.py")
        self.assertEqual(self.head(), fix)
        self.assertEqual(update.blocked_hash(), "", "let go once a round passes")

    def test_a_launchers_fallback_does_not_pull_the_refused_hash(self):
        old = self.head()
        bad = self.publish("ops/doctor.py", "def broken(:\n")
        self.launch("cron_update.py")
        self.assertEqual(update.blocked_hash(), bad)
        path = os.path.join(self.engine, "altention", "update.py")
        with open(path) as f:
            text = f.read()
        with open(path, "w") as f:  # its next round does not load
            f.write("raise RuntimeError('update.py broke after refusing')\n" + text)
        r = self.launch("cron_update.py")
        self.assertEqual(self.head(), old, r.stdout)
        self.assertIn("保底也不拉", self.alerts())

    def test_the_load_check_runs_in_an_isolated_place(self):
        marker = os.path.join(os.path.expanduser("~"), "LOADED-AND-RAN")
        config_text = open(os.path.join(self.up, "altention/config.py")).read()
        self.publish("altention/config.py", "import os\nopen(os.path.expanduser('~/LOADED-AND-RAN'), 'w').close()\n"
                     "raise RuntimeError('on load')\n" + config_text)
        r = self.launch("cron_update.py")
        self.assertIn("載入檢查沒過", self.alerts(), r.stdout)
        self.assertFalse(os.path.exists(marker), "the top-level code ran, but not in this HOME")


class SaysWhatItDid(BrokenSomewhere):
    """cron-update.log says what each round came to: updated X → Y, nothing new, or refused (manager 2026-10-07:
    "this round ended" alone could not show whether a commit came through this way)."""

    def test_updated_nothing_new_refused(self):
        old = self.head()
        new = self.publish("tool.py", "X = 21\n")
        out = self.launch("cron_update.py").stdout
        self.assertIn("更新了 %s → %s" % (old[:12], new[:12]), out)
        self.assertIn("這一輪跑完（結束碼 0）", out)
        self.assertIn("沒有新的（%s）" % new[:12], self.launch("cron_update.py").stdout)
        bad = self.publish("ops/doctor.py", "def broken(:\n")
        self.assertIn("擋下 %s：" % bad[:12], self.launch("cron_update.py").stdout)


class RefusedThenDead(BrokenSomewhere):
    """#code-review on 944c1e1: update.py stops after refusing a hash; a fix pushed later still arrives through the
    sync (fetched before comparing); a skipped round never clears the block."""

    def stale_all(self):
        for name in ("last_ok-update", "last_ok-sync", "last_alive-update"):
            p = os.path.join(self.state, name)
            if os.path.exists(p):
                with open(p) as f:
                    got = json.load(f)
                got["at"] = time.time() - 3 * 3600
                with open(p, "w") as f:
                    json.dump(got, f)

    def test_update_stops_after_refusing_a_fix_still_comes_through_the_sync(self):
        bad = self.publish("ops/doctor.py", "def broken(:\n")
        self.launch("cron_update.py")
        self.assertEqual(update.blocked_hash(), bad)
        fix = self.publish("ops/doctor.py", "X = 'fixed'\n")  # update.py never runs again
        for _ in range(3):
            self.stale_all()
            r = self.launch("cron_sync.py")
            if self.head() == fix:
                break
        self.assertEqual(self.head(), fix, r.stdout[-400:])

    def test_a_skipped_round_keeps_the_block(self):
        bad = self.publish("ops/doctor.py", "def broken(:\n")
        self.launch("cron_update.py")
        before = update.last_alive()
        with open(os.path.join(self.engine, ".git", update.LOCK_NAME), "a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            self.launch("cron_update.py")  # skipped: the lock is taken
        self.assertEqual(update.last_alive(), before, "neither alive written nor the block cleared")
        self.assertEqual(update.blocked_hash(), bad)

    def test_another_checkout_writes_nothing(self):
        wt = os.path.join(self.tmp, "wt")
        git(self.engine, "worktree", "add", "-q", wt)
        with mock.patch.object(update, "ENGINE", wt), mock.patch.dict(os.environ, {"ALT_TEST_ANY_ENGINE": ""}), \
                mock.patch.object(update, "REACHED", []), mock.patch.object(update, "BLOCKED", []):
            update.main([])
        self.assertFalse(os.path.exists(os.path.join(self.state, "last_alive-update")))


class Stacked(BrokenSomewhere):
    """Manager 2026-10-07: a commit put on top of the refused B, unrelated (G, B still broken) or the fix (F), with
    update.py alive and with it stopped, through each path that moves the engine. While update.py checks, the
    others leave a new upstream with B under it to update.py (home-pull and deploy.sh by the stacked rule; the
    sync and the launchers do not stand in at all); update.py looks at everything between HEAD and the new upstream,
    so G is refused again (recorded as G) and F comes in. With update.py stopped, home-pull and deploy.sh still
    hold; the sync's stand-in and both launchers' fallbacks pull it (a fix must get through), never quietly; and
    update.py's first round back says HEAD holds a hash it refused."""

    stale_all = RefusedThenDead.stale_all

    def load(self, name):
        import importlib.util
        spec = importlib.util.spec_from_file_location("stacked_" + name[:-3], os.path.join(ENGINE_REAL, "altention", name))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        mod.ENGINE = self.engine
        return mod

    def held(self, strict=False):
        from altention import sync
        return sync.held_back(sync.Repo(self.engine), "then", strict=strict)

    def kinds(self):
        return [line.split("\t")[2] for line in self.alerts().splitlines() if line.count("\t") >= 3]

    def setup_case(self, which):
        self.old = self.head()
        self.bad = self.publish("ops/doctor.py", "def broken(:\n")
        self.launch("cron_update.py")
        self.assertEqual(update.blocked_hash(), self.bad)
        self.top = self.publish("tool.py", "X = 33\n") if which == "G" else self.publish("ops/doctor.py", "X = 'fixed'\n")

    def alive(self, which):
        self.setup_case(which)
        self.assertIn("被擋", self.held(), "home-pull")
        self.assertIn("被擋", self.held(strict=True), "deploy.sh")
        self.launch("cron_sync.py")
        self.assertEqual(self.head(), self.old, "the sync leaves the engine to a live update.py")
        out = self.launch("cron_update.py").stdout
        if which == "G":
            self.assertEqual(self.head(), self.old, out)
            self.assertEqual(update.blocked_hash(), self.top, "refused again, recorded as G")
            self.assertIn("擋下 %s" % self.top[:12], out)
        else:
            self.assertEqual(self.head(), self.top, out)
            self.assertEqual(update.blocked_hash(), "", "the fix clears the record")
            self.assertEqual(self.held(), "", "and the others go on as usual")
        self.assertNotIn("stacked-on-refused", self.kinds())

    def test_alive_g(self):
        self.alive("G")

    def test_alive_f(self):
        self.alive("F")

    def stopped(self, which, path):
        self.setup_case(which)
        self.stale_all()  # update.py has not come to the end of a round for hours
        self.assertIn("被擋", self.held(), "home-pull still holds")
        self.assertIn("被擋", self.held(strict=True), "deploy.sh still holds")
        self.assertEqual(self.head(), self.old)
        if path == "sync":
            self.launch("cron_sync.py")
        else:
            self.assertEqual(self.load(path).plain_ff(), "快轉好了")
        self.assertEqual(self.head(), self.top, "a fix must get through while nobody checks")
        self.assertIn("stacked-on-refused", self.kinds(), "never quietly")
        self.assertIn(self.bad[:12], self.alerts())
        self.launch("cron_update.py")  # update.py back
        self.assertIn("took-refused", self.kinds())
        self.assertEqual(update.blocked_hash(), "")

    def test_stopped_g_sync(self):
        self.stopped("G", "sync")

    def test_stopped_f_sync(self):
        self.stopped("F", "sync")

    def test_stopped_g_update_launcher(self):
        self.stopped("G", "cron_update.py")

    def test_stopped_f_update_launcher(self):
        self.stopped("F", "cron_update.py")

    def test_stopped_g_sync_launcher(self):
        self.stopped("G", "cron_sync.py")

    def test_stopped_f_sync_launcher(self):
        self.stopped("F", "cron_sync.py")

    def test_equal_to_the_refused_hash_nobody_but_update_and_a_person_pull_it(self):
        self.old = self.head()
        self.bad = self.publish("ops/doctor.py", "def broken(:\n")
        self.launch("cron_update.py")
        self.stale_all()
        for name in ("cron_update.py", "cron_sync.py"):
            self.assertIn("保底也不拉", self.load(name).plain_ff())
        self.launch("cron_sync.py")
        self.assertEqual(self.head(), self.old)
        self.assertIn("被擋", self.held())

    def test_a_record_that_cannot_be_read_stops_deploy_not_home_pull(self):
        self.publish("tool.py", "X = 44\n")
        os.makedirs(self.state, exist_ok=True)
        with open(os.path.join(self.state, "last_alive-update"), "w") as f:
            f.write("{ cut")
        self.assertEqual(self.held(), "", "home-pull: no record, pulled as before")
        self.assertIn("讀不出", self.held(strict=True), "deploy.sh: not known, stopped")


class OneReading(BrokenSomewhere):
    """The refused hash has one reading, and every path that pulls the engine uses it (manager 2026-10-06)."""

    def readers(self):
        import importlib.util
        mods = []
        for name in ("cron_update.py", "cron_sync.py"):
            spec = importlib.util.spec_from_file_location("r_" + name[:-3], os.path.join(ENGINE_REAL, "altention", name))
            m = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(m)
            mods.append(m)
        return {"update": update.blocked_hash, "sync": lambda: sync.refused_hash(note=lambda t: None),
                "cron_update": mods[0].refused, "cron_sync": mods[1].refused}

    def test_all_four_read_the_same(self):
        path = os.path.join(self.state, "last_alive-update")
        os.makedirs(self.state, exist_ok=True)
        cases = {None: "", '{"at": 1, "blocked": {"hash": "abc"}}': "abc", '{"at": 1, "blocked": null}': "",
                 "{ broken": "", "[]": "", '{"blocked": {"hash": 5}}': "", '{"blocked": "abc"}': ""}
        for text, want in cases.items():
            if text is None:
                if os.path.exists(path):
                    os.remove(path)
            else:
                with open(path, "w") as f:
                    f.write(text)
            with mock.patch.object(update, "alert"):
                got = {k: f() for k, f in self.readers().items()}
            self.assertEqual(set(got.values()), {want}, "%r: %r" % (text, got))

    def test_update_unloadable_while_refusing_the_sync_does_not_pull_it_then_takes_the_fix(self):
        bad = self.publish("ops/doctor.py", "def broken(:\n")
        self.launch("cron_update.py")
        self.assertEqual(update.blocked_hash(), bad)
        path = os.path.join(self.engine, "altention", "update.py")
        with open(path) as f:
            text = f.read()
        with open(path, "w") as f:
            f.write("raise RuntimeError('cannot load')\n" + text)
        old = self.head()
        for name in ("last_ok-update", "last_ok-sync", "last_alive-update"):
            p = os.path.join(self.state, name)
            if not os.path.exists(p):
                continue
            with open(p) as f:
                got = json.load(f)
            got["at"] = time.time() - 3600
            with open(p, "w") as f:
                json.dump(got, f)
        r = self.launch("cron_sync.py")
        self.assertEqual(self.head(), old, "the refused hash stays out\n" + r.stdout[-300:])
        fix = self.publish("ops/doctor.py", "X = 'fixed'\n")
        self.launch("cron_sync.py")
        self.assertEqual(self.head(), fix)


class SkippedInARow(Rig):
    def test_skips_are_recorded_with_their_reason_and_cleared_by_a_real_round(self):
        with open(os.path.join(self.engine, ".git", update.LOCK_NAME), "a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            with self.notify:
                update.main([])
                first = json.load(open(os.path.join(self.state, "last_skip-update")))
                update.main([])
        got = json.load(open(os.path.join(self.state, "last_skip-update")))
        self.assertEqual(got["why"], "git 鎖被別人拿著")
        self.assertEqual(got["since"], first["since"], "since kept while the skips go on")
        os.makedirs(os.path.join(self.engine, ".git", "rebase-merge"))
        with self.notify:
            update.main([])
        self.assertIn("殘局", json.load(open(os.path.join(self.state, "last_skip-update")))["why"])
        os.rmdir(os.path.join(self.engine, ".git", "rebase-merge"))
        with self.notify:
            update.main([])
        self.assertFalse(os.path.exists(os.path.join(self.state, "last_skip-update")), "a real round clears it")


class FromAnotherCheckout(Rig):
    """Manager 2026-10-06: from a worktree the four entries do nothing and write no state at all."""

    def test_four_entries_from_a_worktree_leave_the_state_folder_as_it_was(self):
        wt = os.path.join(self.tmp, "wt")
        git(self.engine, "worktree", "add", "-q", wt)
        os.makedirs(self.state, exist_ok=True)
        update.mark_ok("sync")
        before = {n: open(os.path.join(self.state, n), "rb").read() for n in os.listdir(self.state)}
        env = dict(os.environ, ALT_TEST_ANY_ENGINE="")
        home_engine = os.path.join(os.path.expanduser("~"), "altention", "engine")  # the default place
        os.makedirs(os.path.dirname(home_engine), exist_ok=True)
        os.symlink(self.engine, home_engine)  # this machine's engine is the clone, not the worktree
        try:
            for argv in ([sys.executable, os.path.join(wt, "altention", "cron_update.py")],
                         [sys.executable, os.path.join(wt, "altention", "cron_sync.py")],
                         [sys.executable, os.path.join(wt, "altention", "update.py")],
                         [sys.executable, "-m", "altention.sync"]):
                subprocess.run(argv, cwd=wt, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=300)
        finally:
            os.remove(home_engine)
        after = {n: open(os.path.join(self.state, n), "rb").read() for n in os.listdir(self.state)}
        self.assertEqual(after, before)


class CronLog(unittest.TestCase):
    def load(self, name):
        import importlib.util
        spec = importlib.util.spec_from_file_location("log_" + name[:-3], os.path.join(ENGINE_REAL, "altention", name))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def test_the_launchers_keep_their_cron_log_to_500_lines_and_say_why_they_skip(self):
        import io
        from contextlib import redirect_stdout
        for name, log in (("cron_update.py", "cron-update.log"), ("cron_sync.py", "cron-sync.log")):
            mod = self.load(name)
            path = os.path.join(update.state_dir(), log)
            os.makedirs(update.state_dir(), exist_ok=True)
            with open(path, "w") as f:
                f.writelines("old %d\n" % i for i in range(700))
            mod.trim_log()
            with open(path) as f:
                self.assertEqual(len(f.readlines()), 500)
            out = io.StringIO()
            with mock.patch.object(mod, "elsewhere", return_value="這台的引擎在 /x"), redirect_stdout(out):
                self.assertEqual(mod.main(), 0)
            self.assertIn("不是這台的正式引擎：這台的引擎在 /x", out.getvalue())
            os.remove(path)

    def test_core_declares_the_three_lines_outside_the_scheduler(self):
        from altention import plugin
        d = plugin.load(os.path.join(ENGINE_REAL, "altention", "core.plugin.json"), core=True)
        mine = {c["name"]: c for c in d["cron"]}
        for name in ("engine-update", "sync", "watch"):
            self.assertIs(mine[name]["scheduler"], False, name)
        self.assertEqual(mine["watch"]["on"], "bot")
        self.assertIn("cron_update.py", mine["engine-update"]["run"])
        self.assertIn("cron_sync.py", mine["sync"]["run"])
