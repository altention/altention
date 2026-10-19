"""bin/home-git -C: a commit in another repo the sync commits (the secretary's folder), under that repo's own lock."""
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
from unittest import mock

HERE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # the engine


def sh(cwd, *args):
    return subprocess.run(list(args), cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)


class EnginePush(unittest.TestCase):
    """C-44 (manager 2026-10-17, #code-review of 88e7229): `home-git push` of the engine with a step back of the
    supervisor's among what it would send is refused; the origin receives nothing. The real home-git in a throwaway
    engine repository."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="推 ")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.com", GIT_COMMITTER_NAME="t",
                        GIT_COMMITTER_EMAIL="t@example.com", GIT_CEILING_DIRECTORIES=os.path.dirname(self.tmp))
        self.engine = os.path.join(self.tmp, "engine")
        os.makedirs(os.path.join(self.engine, "bin"))
        shutil.copy2(os.path.join(HERE, "bin", "home-git"), os.path.join(self.engine, "bin", "home-git"))
        shutil.copytree(os.path.join(HERE, "altention"), os.path.join(self.engine, "altention"),
                        ignore=shutil.ignore_patterns("tests", "__pycache__"))
        self.origin = os.path.join(self.tmp, "origin.git")
        self.git(self.tmp, "init", "-q", "--bare", self.origin)
        for args in (["init", "-q", "-b", "main"], ["add", "-A"], ["commit", "-qm", "one"], ["remote", "add", "origin", self.origin],
                     ["push", "-q", "-u", "origin", "main"]):
            self.git(self.engine, *args)
        self.upstream = self.git(self.origin, "rev-parse", "main").stdout.strip()

    def git(self, cwd, *args):
        return subprocess.run(["git"] + list(args), cwd=cwd, env=self.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              universal_newlines=True)

    def push(self):
        return subprocess.run([os.path.join(self.engine, "bin", "home-git"), "push", "-q"], cwd=self.engine, env=self.env,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, timeout=60)

    def test_a_step_back_is_refused_and_an_ordinary_commit_is_pushed(self):
        from altention import sync
        subject = "dcbot: step back to 1234567 while packages install (89abcde)"
        self.assertTrue(sync.STEP_BACK_SUBJECT.match(subject))
        self.git(self.engine, "commit", "-q", "--allow-empty", "-m", subject)
        step = self.git(self.engine, "rev-parse", "HEAD").stdout.strip()
        self.git(self.engine, "commit", "-q", "--allow-empty", "-m", "mine on top")
        r = self.push()
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("這台在用舊版等套件，不能推（退路 commit：%s）" % step[:7], r.stderr)
        self.assertIn("bin/home-git rebase --onto %s^ %s" % (step[:7], step[:7]), r.stderr, "the step back under X named, not HEAD^")
        self.assertEqual(self.git(self.origin, "rev-parse", "main").stdout.strip(), self.upstream, "the origin received nothing")
        self.git(self.engine, "reset", "-q", "--hard", self.upstream)
        self.git(self.engine, "commit", "-q", "--allow-empty", "-m", "an ordinary commit")
        r = self.push()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.git(self.origin, "rev-parse", "main").stdout.strip(), self.git(self.engine, "rev-parse", "HEAD").stdout.strip())


class OtherRepo(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="秘書 ")
        os.environ.update({"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com", "GIT_COMMITTER_NAME": "t",
                           "GIT_COMMITTER_EMAIL": "t@example.com"})
        self.folder = os.path.join(self.tmp, "planner")
        os.makedirs(self.folder)
        sh(self.folder, "git", "init", "-q")
        with open(os.path.join(self.folder, "plan.md"), "w") as f:
            f.write("# 計畫\n")

        # home-git from a copy of the engine that is no git repo: a home-git that ignored -C (an older one) would
        # otherwise run `add -A` and commit in the engine these tests run from
        self.ceiling = mock.patch.dict(os.environ, {"GIT_CEILING_DIRECTORIES": os.path.dirname(self.tmp)})
        self.ceiling.start()  # nor any repo above the throwaway folder
        self.engine = os.path.join(self.tmp, "engine")
        os.makedirs(os.path.join(self.engine, "bin"))
        shutil.copy2(os.path.join(HERE, "bin", "home-git"), os.path.join(self.engine, "bin", "home-git"))
        shutil.copytree(os.path.join(HERE, "altention"), os.path.join(self.engine, "altention"),
                        ignore=shutil.ignore_patterns("tests", "__pycache__"))

    def tearDown(self):
        self.ceiling.stop()
        shutil.rmtree(self.tmp)

    def test_dash_c_commits_there_with_its_own_lock(self):
        home_git = os.path.join(self.engine, "bin", "home-git")
        self.assertEqual(sh(self.folder, home_git, "-C", ".", "add", "-A", "--", ".").returncode, 0)
        r = sh(self.folder, home_git, "-C", ".", "commit", "-qm", "plan")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(sh(self.folder, "git", "log", "--format=%s").stdout.strip(), "plan")
        self.assertTrue(os.path.exists(os.path.join(self.folder, ".git", "altention-git.lock")), "that repo's lock")
        self.assertFalse(os.path.exists(os.path.join(self.engine, ".git")), "nothing made in the engine copy")

    def test_a_folder_inside_another_repo_or_none_is_refused(self):
        """Review of d85142e: `-C .` in a folder inside the instance took the whole instance into the commit."""
        home_git = os.path.join(self.engine, "bin", "home-git")
        outer = os.path.join(self.tmp, "instance")
        inner = os.path.join(outer, "secretary")
        os.makedirs(inner)
        sh(outer, "git", "init", "-q")
        for path in (os.path.join(outer, "stray.txt"), os.path.join(inner, "plan.md")):
            with open(path, "w") as f:
                f.write("x\n")
        r = sh(inner, home_git, "-C", ".", "add", "-A", "--", ".")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("不是自己的 repo", r.stderr)
        self.assertEqual(sh(outer, "git", "status", "--porcelain").stdout.count("??"), 2, "nothing added there")
        lone = os.path.join(self.tmp, "lone")
        os.makedirs(lone)
        r = sh(lone, home_git, "-C", ".", "status")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("不是 git repo", r.stderr)
        self.assertNotIn("別的同步", r.stderr)

    def test_the_engine_or_the_instance_itself_is_refused(self):
        """Manager 2026-10-16: -C is for a plugin's data repo; the secretary never commits the engine or the
        instance whole (add -A would take a half-edited plugins.json past the sync's check). Both throwaway repos."""
        home_git = os.path.join(self.engine, "bin", "home-git")
        instance = os.path.join(self.tmp, "instance")
        os.makedirs(instance)
        for repo in (self.engine, instance):
            sh(repo, "git", "init", "-q")
            with open(os.path.join(repo, "half.json"), "w") as f:
                f.write("{\"plugins\": ")
        with mock.patch.dict(os.environ, {"ALT_INSTANCE": instance}):
            for repo, word in ((self.engine, "是引擎"), (instance, "是實例")):
                r = sh(self.tmp, home_git, "-C", repo, "add", "-A", "--", ".")
                self.assertNotEqual(r.returncode, 0, repo)
                self.assertIn(word, r.stderr)
                self.assertEqual(sh(repo, "git", "diff", "--cached", "--name-only").stdout, "", "nothing added")


if __name__ == "__main__":
    unittest.main()
