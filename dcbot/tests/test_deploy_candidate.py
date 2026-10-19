"""deploy.sh --commit (deploy_candidate.py): a reviewed commit becomes a candidate in a throwaway worktree, is
checked there, and only then is the live tree moved; before that not a byte of the live tree changes (manager
2026-10-08: the live cron ran code the tests had not passed yet). Each test has a small repository of its own."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.join(HERE, "..", "deploy_candidate.py")
sys.path.insert(0, os.path.join(HERE, ".."))
import deploy_candidate  # noqa: E402
from tests import tmuxguard  # noqa: F401,E402  (no test reaches a real tmux server)

ENV = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.com", GIT_COMMITTER_NAME="t",
           GIT_COMMITTER_EMAIL="t@example.com", GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_SYSTEM=os.devnull)


class Repo(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="部署 ")  # a blank and non-ASCII in the path, as a home may have
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.live = os.path.join(self.tmp, "live")
        os.makedirs(os.path.join(self.live, "dcbot", "bot"))
        os.makedirs(os.path.join(self.live, "altention"))
        shutil.copy(TOOL, os.path.join(self.live, "dcbot", "deploy_candidate.py"))
        self.write("altention/cron.py", "print('old')\n")
        self.write("altention/other.py", "x = 1\n")
        self.write("dcbot/bot/main.py", "pass\n")
        self.git("init", "-q", "-b", "main")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "first")
        self.wt = os.path.join(self.tmp, "wt")
        self.git("worktree", "add", "-q", "-b", "fix", self.wt, "main")

    def write(self, path, text, where=None):
        full = os.path.join(where or self.live, path)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8") as f:
            f.write(text)

    def read(self, path):
        with open(os.path.join(self.live, path), encoding="utf-8") as f:
            return f.read()

    def git(self, *args, cwd=None):
        r = subprocess.run(["git"] + list(args), cwd=cwd or self.live, env=ENV, stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE, universal_newlines=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout.strip()

    def reviewed(self, files, message="reviewed work", gone=()):
        """A commit on the worktree's branch, as a review would have seen it; its hash."""
        for path, text in files.items():
            self.write(path, text, self.wt)
        for path in gone:
            self.git("rm", "-q", path, cwd=self.wt)
        self.git("add", "-A", cwd=self.wt)
        self.git("commit", "-q", "-m", message, cwd=self.wt)
        return self.git("rev-parse", "HEAD", cwd=self.wt)

    def tool(self, *args):
        r = subprocess.run([sys.executable, os.path.join(self.live, "dcbot", "deploy_candidate.py")] + list(args),
                           cwd=os.path.join(self.live, "dcbot"), env=ENV, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           universal_newlines=True)
        return r.returncode, r.stdout, r.stderr

    def keep(self):
        return os.path.join(self.live, ".git", "altention-deploy")

    def state(self):
        with open(os.path.join(self.keep(), "state.json"), encoding="utf-8") as f:
            return json.load(f)


class Prepare(Repo):
    def test_the_candidate_is_head_plus_what_was_reviewed_and_the_live_tree_is_as_it_was(self):
        h = self.reviewed({"altention/cron.py": "print('new')\n", "altention/added.py": "y = 2\n"}, gone=["altention/other.py"])
        self.write("dcbot/bot/main.py", "someone else's half-done edit\n")  # another channel's work in the live tree
        head = self.git("rev-parse", "HEAD")
        code, out, err = self.tool("prepare", "the message", h)
        self.assertEqual(code, 0, err)
        self.assertIn("候選的 tree 就是審過的 tree", err)
        self.assertIn("CAND=", out)
        s = self.state()
        cand = os.path.join(self.keep(), "candidate")
        self.assertEqual(self.git("rev-parse", "HEAD"), head, "the live branch has not moved")
        self.assertEqual(self.read("altention/cron.py"), "print('old')\n", "what cron runs is the old code")
        self.assertTrue(os.path.exists(os.path.join(self.live, "altention", "other.py")))
        self.assertFalse(os.path.exists(os.path.join(self.live, "altention", "added.py")))
        with open(os.path.join(cand, "altention", "cron.py")) as f:
            self.assertEqual(f.read(), "print('new')\n")
        self.assertFalse(os.path.exists(os.path.join(cand, "altention", "other.py")))
        self.assertEqual(self.git("rev-parse", s["candidate"] + "^"), head)
        self.assertEqual(self.git("diff", "--name-only", head, s["candidate"]).split("\n"),
                         ["altention/added.py", "altention/cron.py", "altention/other.py"])
        self.assertIn("(reviewed %s)" % h[:7], self.git("log", "-1", "--format=%s", s["candidate"]))
        self.assertEqual(self.tool("untouched")[0], 0)

    def test_a_message_that_names_the_reviewed_commit_is_kept_as_written(self):
        h = self.reviewed({"altention/cron.py": "print('new')\n"})
        self.assertEqual(self.tool("prepare", "the change (reviewed aaa1111, %s; rehearsed)" % h[:7], h)[0], 0)
        self.assertEqual(self.git("log", "-1", "--format=%s", self.state()["candidate"]),
                         "the change (reviewed aaa1111, %s; rehearsed)" % h[:7])

    def test_stacked_commits_go_in_as_one_when_every_one_is_named(self):
        first = self.reviewed({"altention/cron.py": "print('first try')\n"}, message="the first, reviewed too")
        h = self.reviewed({"altention/cron.py": "print('second try')\n", "altention/added.py": "y = 2\n"})
        for order in ((first, h), (h, first), (h[:9], first, h)):
            self.assertEqual(self.tool("prepare", "m", *order)[0], 0, order)
            self.assertEqual(self.git("show", self.state()["candidate"] + ":altention/cron.py"), "print('second try')")
            self.assertEqual(self.git("log", "-1", "--format=%s", self.state()["candidate"]),
                             "m (reviewed %s, %s)" % (first[:7], h[:7]))

    def test_a_commit_on_the_way_that_was_not_named_stops_it(self):
        """#code-review-2 2026-10-09: a reviewed commit stacked on one that was not: both went live, the message
        named the reviewed one only (manager: only what was reviewed goes live)."""
        self.reviewed({"altention/other.py": "x = 'never reviewed'\n"}, message="slipped in underneath")
        h = self.reviewed({"altention/cron.py": "print('reviewed')\n"})
        code, out, err = self.tool("prepare", "m", h)
        self.assertEqual(code, 1)
        self.assertIn("slipped in underneath", err)
        self.assertIn("沒審過的不能上線", err)
        self.assertFalse(os.path.exists(os.path.join(self.keep(), "candidate")))
        self.assertEqual(self.read("altention/other.py"), "x = 1\n")

    def test_what_is_named_must_be_one_line_up_from_main(self):
        h = self.reviewed({"altention/cron.py": "print('new')\n"})
        side = os.path.join(self.tmp, "side")
        self.git("worktree", "add", "-q", "-b", "side", side, "main")
        self.write("altention/other.py", "x = 5\n", side)
        self.git("commit", "-q", "-am", "on another line", cwd=side)
        other = self.git("rev-parse", "HEAD", cwd=side)
        code, out, err = self.tool("prepare", "m", h, other)
        self.assertEqual(code, 1)
        self.assertIn("不在同一條線上", err)
        code, out, err = self.tool("prepare", "m", h, self.git("rev-parse", "HEAD"))
        self.assertEqual(code, 1)
        self.assertIn("已經在 main 裡", err)

    def test_another_commit_on_the_same_file_since_the_base_stops_it(self):
        """Manager 2026-10-08: reviewed against one state of the file, it would land on another."""
        h = self.reviewed({"altention/cron.py": "print('new')\n"})
        self.write("altention/cron.py", "print('someone else')\n")
        self.git("commit", "-q", "-am", "another channel's commit")
        code, out, err = self.tool("prepare", "m", h)
        self.assertEqual(code, 1)
        self.assertIn("another channel's commit", err)
        self.assertFalse(os.path.exists(os.path.join(self.keep(), "candidate")))

    def test_another_commit_elsewhere_since_the_base_is_no_matter(self):
        h = self.reviewed({"altention/cron.py": "print('new')\n"})
        self.write("altention/other.py", "x = 2\n")
        self.git("commit", "-q", "-am", "elsewhere")
        self.assertEqual(self.tool("prepare", "m", h)[0], 0)
        c = self.state()["candidate"]
        self.assertEqual(self.git("show", c + ":altention/other.py"), "x = 2", "what main gained stays")
        self.assertEqual(self.git("diff", "--name-only", "HEAD", c), "altention/cron.py")

    def test_an_uncommitted_edit_of_a_file_about_to_change_is_not_overwritten(self):
        h = self.reviewed({"altention/cron.py": "print('new')\n"})
        self.write("altention/cron.py", "half-done\n")
        code, out, err = self.tool("prepare", "m", h)
        self.assertEqual(code, 1)
        self.assertIn("還沒提交的修改", err)
        self.assertEqual(self.read("altention/cron.py"), "half-done\n")

    def test_nothing_to_deploy(self):
        self.assertEqual(self.tool("prepare", "m", self.git("rev-parse", "HEAD"))[0], 1)
        self.assertEqual(self.tool("prepare", "m", "0000000")[0], 1)

    def test_what_the_last_deploy_left_is_cleared(self):
        h = self.reviewed({"altention/cron.py": "print('new')\n"})
        self.assertEqual(self.tool("prepare", "m", h)[0], 0)
        os.makedirs(os.path.join(self.keep(), "tests"))
        self.write(".git/altention-deploy/tests/altention", "the failed run's output\n")
        self.assertEqual(self.tool("prepare", "m", h)[0], 0)
        self.assertFalse(os.path.exists(os.path.join(self.keep(), "tests")))
        self.assertEqual(self.git("worktree", "list").count("altention-deploy"), 1)


class Land(Repo):
    def prepared(self):
        h = self.reviewed({"altention/cron.py": "print('new')\n"})
        self.assertEqual(self.tool("prepare", "m", h)[0], 0)
        return self.state()

    def test_the_branch_moves_and_other_channels_edits_stay(self):
        self.write("dcbot/bot/main.py", "half-done elsewhere\n")
        s = self.prepared()
        self.assertEqual(self.tool("land", s["candidate"], s["head"])[0], 0)
        self.assertEqual(self.git("rev-parse", "HEAD"), s["candidate"])
        self.assertEqual(self.read("altention/cron.py"), "print('new')\n")
        self.assertEqual(self.read("dcbot/bot/main.py"), "half-done elsewhere\n")
        self.assertEqual(self.tool("done")[0], 0)
        self.assertFalse(os.path.exists(self.keep()))
        self.assertNotIn("altention-deploy", self.git("worktree", "list"))

    def test_main_moved_meanwhile_asks_for_a_new_candidate_and_changes_nothing(self):
        s = self.prepared()
        self.write("altention/other.py", "x = 3\n")
        self.git("commit", "-q", "-am", "landed meanwhile")
        head = self.git("rev-parse", "HEAD")
        self.assertEqual(self.tool("land", s["candidate"], s["head"])[0], 3)
        self.assertEqual(self.git("rev-parse", "HEAD"), head)
        self.assertEqual(self.read("altention/cron.py"), "print('old')\n")

    def test_a_file_edited_meanwhile_is_not_overwritten(self):
        s = self.prepared()
        self.write("altention/cron.py", "edited during the tests\n")
        code, out, err = self.tool("land", s["candidate"], s["head"])
        self.assertEqual(code, 1)
        self.assertEqual(self.read("altention/cron.py"), "edited during the tests\n")
        code, out, err = self.tool("untouched")
        self.assertEqual(code, 1, "and the live tree is said to differ")
        self.assertIn("不一樣", out)

    def test_what_others_did_meanwhile_is_said_apart_and_is_no_alarm(self):
        """#code-review 2026-10-09: an unrelated edit by another channel, or the sync's commit, during a failed
        deploy raised "the live tree is not as it was". A false alarm here teaches no one to read the true one."""
        s = self.prepared()
        self.assertEqual(self.tool("untouched")[0], 0)
        self.write("stray.txt", "x")
        self.write("altention/other.py", "x = 9\n")
        self.git("commit", "-q", "-m", "the sync's commit", "altention/other.py")
        code, out, err = self.tool("untouched")
        self.assertEqual(code, 0)
        self.assertIn("沒有被這次部署動過", out)
        self.assertIn("部署期間別處有變動", out)
        self.assertIn("the sync's commit", out)
        self.assertIn("stray.txt", out)
        self.assertNotIn("✗", out)
        self.write("altention/cron.py", "changed after all\n")
        code, out, err = self.tool("untouched")
        self.assertEqual(code, 1)
        self.assertIn("✗✗✗", out)

    def test_the_candidate_in_mains_history_after_a_failure_is_said(self):
        """Manager 2026-10-09: also when the sync has committed on top of it since: HEAD is not the candidate
        then, and the candidate is on main all the same."""
        s = self.prepared()
        self.git("merge", "--ff-only", "-q", s["candidate"])
        self.git("checkout", "-q", s["head"], "--", "altention/cron.py")  # the files as before, the branch not
        self.assertEqual(self.tool("untouched")[0], 1)
        self.write("altention/other.py", "x = 7\n")
        self.git("commit", "-q", "-m", "the sync's commit on top", "altention/other.py")
        code, out, err = self.tool("untouched")
        self.assertEqual(code, 1)
        self.assertIn("候選已經在 main 的歷史裡", out)

    def test_only_the_candidate_this_deploy_checked_is_landed(self):
        """#code-review 2026-10-09: a second deploy made its candidate while the first ran its checks; the first
        then landed the second's, unchecked."""
        a = self.prepared()
        bad = self.reviewed({"altention/cron.py": "raise SystemExit('not checked')\n"})
        self.assertEqual(self.tool("prepare", "the other deploy", a["reviewed"], bad)[0], 0)
        head = self.git("rev-parse", "HEAD")
        code, out, err = self.tool("land", a["candidate"], a["head"])
        self.assertEqual(code, 1)
        self.assertIn("不是這次檢查過的那個", err)
        self.assertEqual(self.git("rev-parse", "HEAD"), head)
        self.assertEqual(self.read("altention/cron.py"), "print('old')\n")


class Renames(Repo):
    """#code-review 2026-10-09: git's list of changed files followed renames and named only the new file: the
    old one stayed in the candidate, every check made from that list passed, and both were on main."""

    def moved(self, change=None, to="altention/cron_renamed.py", other_commit=False):
        if other_commit:
            self.write("dcbot/bot/main.py", "pass  # elsewhere\n")
            self.git("commit", "-q", "-am", "elsewhere on main")
        os.makedirs(os.path.dirname(os.path.join(self.wt, to)), exist_ok=True)
        self.git("mv", "altention/cron.py", to, cwd=self.wt)
        h = self.reviewed({to: change} if change else {}, message="moved")
        code, out, err = self.tool("prepare", "m", h)
        self.assertEqual(code, 0, err)
        s = self.state()
        names = self.git("ls-tree", "-r", "--name-only", s["candidate"]).split("\n")
        self.assertNotIn("altention/cron.py", names, "the old name is gone from the candidate")
        self.assertIn(to, names)
        self.assertIn("altention/cron.py", s["paths"])
        self.assertEqual(self.tool("land", s["candidate"], s["head"])[0], 0)
        self.assertFalse(os.path.exists(os.path.join(self.live, "altention", "cron.py")), "and from the live tree")
        return h, s

    def test_a_rename(self):
        h, s = self.moved()
        self.assertEqual(self.git("rev-parse", s["candidate"] + "^{tree}"), self.git("rev-parse", h + "^{tree}"))

    def test_a_rename_with_a_change(self):
        self.moved(change="print('old')\nprint('and more')\n")

    def test_a_folder_moved(self):
        self.moved(to="core/sched/cron.py")

    def test_a_rename_with_another_commit_on_main(self):
        h, s = self.moved(change="print('old')\nprint('and more')\n", other_commit=True)
        self.assertEqual(self.read("dcbot/bot/main.py"), "pass  # elsewhere\n")


class Touched(Repo):
    def test_both_names_of_a_staged_rename_and_names_with_blanks(self):
        self.write("dcbot/bot/a b.py", "x\n")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "more")
        self.git("mv", "dcbot/bot/main.py", "dcbot/bot/main2.py")
        self.write("dcbot/bot/a b.py", "y\n")
        self.write("altention/cron.py", "edited\n")
        self.write("altention/other.py", "edited\n")
        code, out, err = self.tool("in-place", "altention/cron.py")
        self.assertEqual(code, 0, err)
        self.assertEqual(out.split("\n")[:-1], ["altention/cron.py"], "nothing tools import in this small repo: bot/ may be edited")
        r = subprocess.run([sys.executable, "-B", "-c", "import deploy_candidate as d; print(sorted(d.touched(['altention/cron.py'])))"],
                           cwd=os.path.join(self.live, "dcbot"), env=ENV, stdout=subprocess.PIPE, universal_newlines=True)
        self.assertEqual(r.stdout.strip(), str(sorted(["altention/cron.py", "dcbot/bot/a b.py", "dcbot/bot/main.py", "dcbot/bot/main2.py"])))


class Upstream(Repo):
    """#code-review-2 2026-10-09: the upstream had a commit this machine had not: the push after landing was
    refused, the commit put on top of the upstream's, and the tree that went out was one no check had run on."""

    def setUp(self):
        Repo.setUp(self)
        self.origin = os.path.join(self.tmp, "origin.git")
        self.git("clone", "-q", "--bare", self.live, self.origin, cwd=self.tmp)
        self.git("remote", "add", "origin", self.origin)
        self.git("fetch", "-q", "origin")
        self.git("branch", "-q", "--set-upstream-to=origin/main", "main")

    def another_machine_pushes(self):
        there = os.path.join(self.tmp, "there")
        if not os.path.exists(there):
            self.git("clone", "-q", self.origin, there, cwd=self.tmp)
        self.write("altention/other.py", "x = 'from another machine %d'\n" % len(os.listdir(self.tmp)), there)
        self.git("commit", "-q", "-am", "another machine's commit", cwd=there)
        self.git("push", "-q", "origin", "HEAD:main", cwd=there)

    def test_an_upstream_ahead_stops_the_deploy_before_any_candidate(self):
        h = self.reviewed({"altention/cron.py": "print('new')\n"})
        self.another_machine_pushes()
        code, out, err = self.tool("prepare", "m", h)
        self.assertEqual(code, 1)
        self.assertIn("home-pull", err)
        self.assertIn("1 個 commit", err)
        self.assertFalse(os.path.exists(os.path.join(self.keep(), "candidate")))

    def test_an_upstream_level_or_behind_is_no_matter(self):
        h = self.reviewed({"altention/cron.py": "print('new')\n"})
        self.assertEqual(self.tool("prepare", "m", h)[0], 0)
        self.write("altention/other.py", "x = 2\n")
        self.git("commit", "-q", "-am", "not pushed yet")
        self.assertEqual(self.tool("prepare", "m", h)[0], 0)

    def test_an_upstream_that_got_ahead_during_the_checks_stops_the_landing(self):
        h = self.reviewed({"altention/cron.py": "print('new')\n"})
        self.assertEqual(self.tool("prepare", "m", h)[0], 0)
        s = self.state()
        self.another_machine_pushes()
        head = self.git("rev-parse", "HEAD")
        code, out, err = self.tool("land", s["candidate"], s["head"])
        self.assertEqual(code, 1)
        self.assertIn("home-pull", err)
        self.assertEqual(self.git("rev-parse", "HEAD"), head)
        self.assertEqual(self.read("altention/cron.py"), "print('old')\n")
        self.assertEqual(self.tool("untouched")[0], 0)

    def test_an_upstream_that_cannot_be_reached_lets_the_checks_run_and_stops_the_landing(self):
        """Manager 2026-10-09: not known is not "nothing new": the candidate may be made and checked, not landed."""
        h = self.reviewed({"altention/cron.py": "print('new')\n"})
        self.git("remote", "set-url", "origin", os.path.join(self.tmp, "gone.git"))
        code, out, err = self.tool("prepare", "m", h)
        self.assertEqual(code, 0, err)
        self.assertIn("fetch 失敗", err)
        s = self.state()
        head = self.git("rev-parse", "HEAD")
        code, out, err = self.tool("land", s["candidate"], s["head"])
        self.assertEqual(code, 1)
        self.assertIn("查不了 origin", err)
        self.assertIn("正式樹不動", err)
        self.assertEqual(self.git("rev-parse", "HEAD"), head)
        self.assertEqual(self.read("altention/cron.py"), "print('old')\n")
        self.assertEqual(self.tool("untouched")[0], 0)
        self.git("remote", "set-url", "origin", self.origin)
        self.assertEqual(self.tool("land", s["candidate"], s["head"])[0], 0, "reachable again: the same candidate lands")

    def test_the_fetch_before_the_candidate_waits_for_the_git_lock(self):
        import fcntl
        h = self.reviewed({"altention/cron.py": "print('new')\n"})
        with open(os.path.join(self.live, ".git", "altention-git.lock"), "a") as held:
            fcntl.flock(held, fcntl.LOCK_EX)
            p = subprocess.Popen([sys.executable, os.path.join(self.live, "dcbot", "deploy_candidate.py"), "prepare", "m", h],
                                 cwd=os.path.join(self.live, "dcbot"), env=ENV, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            try:
                p.wait(timeout=2.5)
                self.fail("did not wait for the lock")
            except subprocess.TimeoutExpired:
                pass
            self.assertFalse(os.path.exists(os.path.join(self.keep(), "candidate")), "nothing made while it waits")
        self.assertEqual(p.wait(timeout=30), 0)
        p.stdout.close()
        p.stderr.close()


class StepBack(Repo):
    """C-44 (manager 2026-10-17, #code-review of 88e7229): a step back of the supervisor's (only on this machine) is
    never pushed by deploy.sh, either way it is used: refused before anything is done, the origin receives nothing.
    The real deploy.sh, in this test's repository; the upstream did not move."""

    SUBJECT = "dcbot: step back to 1234567 while packages install (89abcde)"

    def setUp(self):
        super().setUp()
        shutil.copy(os.path.join(HERE, "..", "deploy.sh"), os.path.join(self.live, "dcbot", "deploy.sh"))
        self.origin = os.path.join(self.tmp, "origin.git")
        self.git("init", "-q", "--bare", self.origin)
        self.git("remote", "add", "origin", self.origin)
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "with deploy.sh")
        self.git("push", "-q", "-u", "origin", "main")
        self.write("altention/other.py", "x = 0\n")  # what the step back puts back
        self.git("commit", "-q", "-am", self.SUBJECT)
        self.upstream = self.git("rev-parse", "main", cwd=self.origin)

    def deploy(self, *args):
        env = dict(ENV, DEPLOY_NO_GUIDE="1", GIT_CEILING_DIRECTORIES=os.path.dirname(self.tmp))
        return subprocess.run(["bash", os.path.join(self.live, "dcbot", "deploy.sh")] + list(args), cwd=self.live, env=env,
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True, timeout=120)

    def test_both_ways_are_refused_and_nothing_reaches_the_origin(self):
        sys.path.insert(0, os.path.join(HERE, "..", ".."))  # the engine: alone, only dcbot/ is on the path
        from altention import sync
        self.assertTrue(sync.STEP_BACK_SUBJECT.match(self.SUBJECT), "the subject the supervisor writes")
        fix = self.reviewed({"dcbot/requirements.txt": "newthing==1.1\n"}, "the fix")
        self.write("dcbot/bot/main.py", "pass  # edited here\n")
        for args in ((["fix the bot"]), (["--commit", fix, "fix the bot"])):
            with self.subTest(args=args[0]):
                r = self.deploy(*args)
                self.assertEqual(r.returncode, 1, r.stdout)
                self.assertIn("這台在用舊版等套件，不能推", r.stdout)
                step = self.git("rev-parse", "--short=7", "HEAD")
                self.assertIn("bin/home-git rebase --onto %s^ %s" % (step, step), r.stdout, "the step back named, not HEAD^")
                self.assertIn("SUPERVISOR-RESCUE.md", r.stdout)
                self.assertEqual(self.git("rev-parse", "main", cwd=self.origin), self.upstream, "the origin received nothing")
                self.assertEqual(self.git("log", "-1", "--format=%s"), self.SUBJECT, "nothing committed on top")
        self.git("checkout", "-q", "--", "dcbot/bot/main.py")
        self.git("commit", "-q", "--allow-empty", "-m", "a commit on top of it")
        r = self.deploy("fix the bot")
        self.assertEqual(r.returncode, 1, "anywhere among what would be pushed\n" + r.stdout)
        self.assertIn("不能推", r.stdout)
        step = self.git("rev-parse", "--short=7", "HEAD^")
        self.assertIn("bin/home-git rebase --onto %s^ %s" % (step, step), r.stdout, "under another commit: still the step back named")


class InPlace(unittest.TestCase):
    def test_what_the_tools_import_runs_without_a_restart(self):
        live = deploy_candidate.live_modules()
        for mod in ("events", "tmuxio", "config"):  # what the tools import, or could (an import inside a function counts)
            self.assertIn("dcbot/bot/%s.py" % mod, live)
        self.assertNotIn("dcbot/bot/main.py", live)
        self.assertNotIn("dcbot/bot/tmux_bridge.py", live)
        self.assertIn("dcbot/bot/__init__.py", live, "the package's own file runs with whatever module of it a tool imports")
        self.assertFalse(deploy_candidate.in_place_ok("dcbot/bot/__init__.py", live))
        self.assertFalse(deploy_candidate.in_place_ok("altention/connectors/claude_code/screen.py", live), "the engine's: every machine runs it")

    def test_the_tool_on_other_machines_loads_neither_the_bots_settings_nor_its_wording(self):
        """Stage 3b step 1.8, as it really runs: tools/dcremote loaded, and its commands that need no pane run, in a
        home with nothing in it. (live_modules above still lists config.py: tmuxio's functions for another machine
        import bot/remote.py, which reads it; dcremote never calls them.)"""
        tool = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools", "dcremote")
        code = ("import importlib.machinery, importlib.util, io, json, sys, contextlib\n"
                "l = importlib.machinery.SourceFileLoader('dcremote_mod', sys.argv[1])\n"
                "m = importlib.util.module_from_spec(importlib.util.spec_from_loader('dcremote_mod', l)); l.exec_module(m)\n"
                "out = io.StringIO()\n"
                "for argv in (['info'], ['panes'], ['backfill', '%999', 'nosuch', '']):\n"
                "    with contextlib.redirect_stdout(out):\n"
                "        m.main(argv)\n"
                "print(json.dumps(sorted(k for k in sys.modules if k == 'bot' or k.startswith('bot.'))))\n")
        with tempfile.TemporaryDirectory() as home:
            env = dict(os.environ, HOME=home, DCBOT_ENV=os.path.join(home, "none"), TMUX_TMPDIR=home)
            env.pop("CLAUDE_CONFIG_DIR", None)
            got = subprocess.run([sys.executable, "-c", code, tool], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                 universal_newlines=True, timeout=60)
        self.assertEqual(got.returncode, 0, got.stderr[-600:])
        loaded = json.loads(got.stdout.strip().splitlines()[-1])
        self.assertIn("bot.tmuxio", loaded)
        self.assertIn("bot.envfile", loaded, "one name of the settings of a bot on that machine: its tmux server, to leave it alone")
        for mod in ("bot.config", "bot.sessions", "bot.remote", "bot.postbox"):
            self.assertNotIn(mod, loaded)

    def test_only_what_nothing_runs_before_the_restart_may_be_edited_in_place(self):
        live = deploy_candidate.live_modules()
        for path in ("dcbot/bot/main.py", "dcbot/bot/tmux_bridge.py", "dcbot/tests/test_safety.py", "dcbot/CLAUDE.md",
                     "docs/gotchas.md", "dcbot/requirements.txt", "dcbot/supervisor.py", "altention/tests/test_x.py"):
            self.assertTrue(deploy_candidate.in_place_ok(path, live), path)
        for path in ("dcbot/bot/events.py", "altention/connectors/claude_code/hooks.py", "dcbot/tools/dchook", "dcbot/tools/dcremote",
                     "dcbot/deploy.sh", "dcbot/deploy_candidate.py", "altention/cron_schedule.py", "chatlog/chatlog.py",
                     "progress/progress.py", "progress/plugin.json", "altention/dc/post.py", "ops/doctor.py", "tools/privacy-scan", "dcbot/install.sh"):
            self.assertFalse(deploy_candidate.in_place_ok(path, live), path)
        self.assertFalse(deploy_candidate.in_place_ok("dcbot/bot/main.py", None), "the tools not readable: all of bot/ counts")

    def test_a_module_reached_only_through_another_counts(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        os.makedirs(os.path.join(tmp, "tools"))
        os.makedirs(os.path.join(tmp, "bot"))
        files = {"tools/hook": "#!/usr/bin/env python3\nimport sys\ndef f():\n    from bot import a\n",
                 "bot/a.py": "from . import b\nfrom .c import x\n", "bot/b.py": "import os\n", "bot/c.py": "from bot.d import y\n",
                 "bot/d.py": "", "bot/alone.py": "from . import a\n"}
        for name, text in files.items():
            with open(os.path.join(tmp, name), "w") as f:
                f.write(text)
        self.assertEqual(deploy_candidate.live_modules(tmp), {"dcbot/bot/%s.py" % m for m in ("a", "b", "c", "d", "__init__")})
        with open(os.path.join(tmp, "tools", "hook"), "w") as f:
            f.write("#!/usr/bin/env python3\ndef broken(:\n")
        self.assertIsNone(deploy_candidate.live_modules(tmp))


if __name__ == "__main__":
    unittest.main()
