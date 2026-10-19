"""ops/release.py: one public commit from a committed tree, only when the privacy scan, the COMPAT register and the
public repository's own commits allow it. Throwaway repositories only (a bare one stands for the public repo)."""
import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))))
from altention import testing as _testing  # noqa: E402
_testing.guard_home()  # tests never touch the real home (altention/testing.py)
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import release  # noqa: E402

AUTHOR = "Altention Release <noreply@example.com>"
CHANGES = "# Changes\n\nWhat each release brought.\n\n## v0.2.0 — 2026-11-01\n\n- second\n\n## v0.1.0 — 2026-10-20\n\n- first\n- 中文 too\n"
HASH, SLASH = "# COMPAT" + ":", "// COMPAT" + ":"  # spelt apart, so this file holds no marker of its own
REGISTER = [{"file": "tool.py", "text": "old place, until after the release", "status": "after-release", "why": "x"},
            {"file": "settings.json", "text": "(json)", "status": "after-release", "why": "no comments in JSON"}]


def git(repo, *args, env=None):
    p = subprocess.run(["git", "-C", repo, "-c", "user.name=alice", "-c", "user.email=alice@example.com"] + list(args),
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, env=env)
    if p.returncode:
        raise AssertionError("git %s: %s" % (args, p.stderr))
    return p.stdout.strip()


class Rig(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="發佈 release ")
        self.private = os.path.join(self.tmp, "私人 engine")
        self.public = os.path.join(self.tmp, "public repo.git")
        git(self.tmp, "init", "-q", "--bare", self.public)
        git(self.public, "symbolic-ref", "HEAD", "refs/heads/main")
        os.makedirs(self.private)
        git(self.private, "init", "-q")
        git(self.private, "checkout", "-q", "-b", "main")
        self.write("tool.py", "X = 1  %s old place, until after the release\n" % HASH)
        self.write("README.md", "Mentions COMPAT: in prose, which is not a marker.\n")
        self.write("CHANGELOG.md", CHANGES)
        self.write("docs/compat.json", json.dumps(REGISTER))
        self.write("資料 夾/說明 檔.txt", "hello\n")
        self.commit("one")
        self.origin = os.path.join(self.tmp, "private origin.git")  # this repository's own remote (C-55)
        git(self.tmp, "init", "-q", "--bare", self.origin)
        git(self.private, "remote", "add", "origin", self.origin)
        git(self.private, "push", "-q", "-u", "origin", "main")
        self.inst = os.path.join(self.tmp, "實例 instance")
        os.makedirs(self.inst)
        self.settings({"repo": self.public, "author": AUTHOR})
        with open(os.path.join(self.inst, "privacy.json"), "w") as f:
            json.dump({"terms": ["secretproject"]}, f)
        self.env = mock.patch.dict(os.environ, {"ALT_INSTANCE": self.inst})
        self.env.start()
        self.engine = mock.patch.object(release, "ENGINE", self.private)
        self.engine.start()

    def tearDown(self):
        self.engine.stop()
        self.env.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def settings(self, rel):
        with open(os.path.join(self.inst, "plugins.json"), "w") as f:
            json.dump({"format": 1, "release": rel}, f)

    def write(self, rel, text):
        path = os.path.join(self.private, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)

    def commit(self, msg):
        git(self.private, "add", "-A")
        git(self.private, "commit", "-q", "-m", msg)
        return git(self.private, "rev-parse", "HEAD")

    def run_(self, *argv):
        out = io.StringIO()
        with redirect_stdout(out):
            code = release.main(list(argv))
        return code, out.getvalue()

    def public_main(self):
        p = subprocess.run(["git", "-C", self.public, "rev-parse", "-q", "--verify", "refs/heads/main"],
                           stdout=subprocess.PIPE, universal_newlines=True)
        return p.stdout.strip()


class Publishes(Rig):
    def test_check_passes_and_makes_nothing(self):
        code, out = self.run_("check")
        self.assertEqual(code, 0, out)
        self.assertIn("可以發佈", out)
        self.assertEqual(self.public_main(), "")

    def test_first_release_kept_until_push_then_pushed_with_its_tag(self):
        head = git(self.private, "rev-parse", "HEAD")
        self.write("half done.txt", "not committed\n")  # the working directory is never what is published
        code, out = self.run_("publish", "0.1.0")
        self.assertEqual(code, 0, out)
        self.assertEqual(self.public_main(), "", "not pushed without --push")
        made = git(self.private, "rev-parse", release.NEXT_REF)
        code, out = self.run_("publish", "0.1.0", "--push")
        self.assertEqual(code, 0, out)
        first = self.public_main()
        self.assertTrue(first)
        shown = git(self.public, "show", "-s", "--format=%T|%P|%an <%ae>|%cn <%ce>|%B", first).split("|")
        self.assertEqual(shown[0], git(self.private, "rev-parse", head + "^{tree}"), "the committed tree, exactly")
        self.assertEqual(shown[1], "", "the first release has no parent: no private history")
        self.assertEqual(shown[2:4], [AUTHOR, AUTHOR])
        self.assertTrue(shown[4].startswith("Altention v0.1.0\n\n- first\n- 中文 too"), shown[4])
        self.assertNotIn("v0.2.0", shown[4])
        self.assertEqual(git(self.public, "rev-parse", "v0.1.0^{commit}"), first)
        self.assertNotIn("half done.txt", git(self.public, "ls-tree", "-r", "--name-only", first))
        self.assertEqual(made.split()[0][:7] and git(self.public, "rev-parse", first + "^{tree}"),
                         git(self.private, "rev-parse", made + "^{tree}"))
        self.write("tool.py", "X = 2  %s old place, until after the release\n" % HASH)
        self.commit("two")
        code, out = self.run_("publish", "0.2.0", "--push")
        self.assertEqual(code, 0, out)
        second = self.public_main()
        self.assertEqual(git(self.public, "show", "-s", "--format=%P", second), first, "on top of the public main")
        self.assertEqual(git(self.public, "rev-list", "--count", second), "2", "nothing of the private history")

    def test_the_public_main_moved_after_it_was_read_nothing_half_done(self):
        self.assertEqual(self.run_("publish", "0.1.0", "--push")[0], 0)
        self.write("tool.py", "X = 3  %s old place, until after the release\n" % HASH)
        self.commit("three")
        real = release.make_commit

        def meanwhile(*a, **k):
            made = real(*a, **k)
            other = os.path.join(self.tmp, "someone")
            git(self.tmp, "clone", "-q", self.public, other)
            with open(os.path.join(other, "theirs.txt"), "w") as f:
                f.write("x\n")
            git(other, "add", "-A")
            git(other, "commit", "-q", "-m", "theirs")
            git(other, "push", "-q", "origin", "HEAD:main")
            return made
        moved = None
        with mock.patch.object(release, "make_commit", meanwhile):
            code, out = self.run_("publish", "0.2.0", "--push")
            moved = self.public_main()
        self.assertEqual(code, 2, out)
        self.assertEqual(git(self.public, "show", "-s", "--format=%s", moved), "theirs", "theirs stays, not overwritten")
        self.assertNotIn("v0.2.0", git(self.public, "tag"))


class Refuses(Rig):
    def refused(self, *argv, says):
        code, out = self.run_(*(argv or ("publish", "0.1.0", "--push")))
        self.assertEqual(code, 1, out)
        self.assertIn(says, out)
        self.assertEqual(self.public_main(), "", "nothing pushed")
        return out

    def test_something_personal_in_the_tree(self):
        self.write("notes.txt", "about secretproject\n")
        self.commit("oops")
        self.refused(says="個資掃描找到東西")

    def test_no_blacklist_no_release(self):
        os.remove(os.path.join(self.inst, "privacy.json"))
        self.refused(says="沒有實例的黑名單")

    def test_an_unregistered_or_unfinished_compat(self):
        self.write("ops/x.sh", "true  %s something new\n" % HASH)
        self.write("web/x.js", "f();  %s another\n" % SLASH)
        self.commit("new compat")
        out = self.refused(says="沒有登記")
        self.assertIn("ops/x.sh", out)
        self.assertIn("web/x.js", out)
        self.write("ops/x.sh", "true\n")
        self.write("web/x.js", "f();\n")
        reg = [dict(REGISTER[0], status="before-release")] + REGISTER[1:]
        self.write("docs/compat.json", json.dumps(reg))
        self.commit("not after-release")
        self.refused(says="不是 after-release")

    def test_a_register_missing_or_broken(self):
        for text, says in ((None, "不在要發佈的樹裡"), ("{ broken", "不是合法的 JSON"),
                           (json.dumps([{"file": "tool.py", "status": "after-release"}]), "每一項都有文字的")):
            with self.subTest(says=says):
                if text is None:
                    os.remove(os.path.join(self.private, "docs", "compat.json"))
                else:
                    self.write("docs/compat.json", text)
                self.commit(says)
                self.refused(says=says)

    def test_a_registered_line_gone_is_only_said(self):
        self.write("docs/compat.json", json.dumps(REGISTER + [dict(REGISTER[0], file="gone.py")]))
        self.commit("gone")
        code, out = self.run_("check")
        self.assertEqual(code, 0, out)
        self.assertIn("gone.py", out)
        self.assertIn("要人工核對：settings.json", out, "what has no comment is listed for a person")

    def test_a_tag_refused_takes_the_main_back_with_it(self):
        """The main and the tag in one atomic push: the public side refusing the tag leaves the main as it was."""
        hook = os.path.join(self.public, "hooks", "update")
        with open(hook, "w") as f:
            f.write('#!/bin/sh\ncase "$1" in refs/tags/*) echo "no tags here" >&2; exit 1;; esac\nexit 0\n')
        os.chmod(hook, 0o755)
        code, out = self.run_("publish", "0.1.0", "--push")
        self.assertEqual(code, 2, out)
        self.assertEqual(self.public_main(), "", "no main without its tag")
        os.remove(hook)
        self.assertEqual(self.run_("publish", "0.1.0", "--push")[0], 0)
        self.assertEqual(git(self.public, "rev-parse", "v0.1.0^{commit}"), self.public_main())

    def test_a_contribution_not_picked_back(self):
        self.assertEqual(self.run_("publish", "0.1.0", "--push")[0], 0)
        other = os.path.join(self.tmp, "contributor")
        git(self.tmp, "clone", "-q", self.public, other)
        with open(os.path.join(other, "fix.txt"), "w") as f:
            f.write("their fix\n")
        git(other, "add", "-A")
        git(other, "commit", "-q", "-m", "their fix")
        git(other, "push", "-q", "origin", "HEAD:main")
        theirs = git(other, "rev-parse", "HEAD")
        code, out = self.run_("publish", "0.2.0")
        self.assertEqual(code, 1, out)
        self.assertIn(theirs[:12], out)
        git(self.private, "fetch", "-q", self.public, "main")
        git(self.private, "cherry-pick", "-x", theirs)
        code, out = self.run_("publish", "0.2.0", "--push")
        self.assertEqual(code, 0, out)
        self.assertIn("fix.txt", git(self.public, "ls-tree", "-r", "--name-only", "main"))
        self.assertEqual(git(self.public, "show", "-s", "--format=%P", "main"), theirs, "on top of their commit")

    def test_versions(self):
        self.refused("publish", "1.0", says="X.Y.Z")
        self.refused("publish", "0.3.0", says="沒有「## v0.3.0」")
        self.assertEqual(self.run_("publish", "0.2.0", "--push")[0], 0)
        code, out = self.run_("publish", "0.2.0")
        self.assertEqual(code, 1)
        self.assertIn("已經有 tag v0.2.0", out)
        code, out = self.run_("publish", "0.1.0")
        self.assertEqual(code, 1)
        self.assertIn("不比 v0.1.0 舊", out)

    def test_something_personal_in_the_message(self):
        """The message comes from CHANGELOG.md, which the tree's scan already covers; the message's own scan is
        the second look, at exactly what is published."""
        self.write("CHANGELOG.md", CHANGES.replace("- first", "- first, thanks to secretproject"))
        self.commit("changelog")
        self.refused(says="個資掃描找到東西")
        head = git(self.private, "rev-parse", "HEAD")
        said = []
        self.assertFalse(release.meta_scan(head, "Altention v0.1.0\n\n- about secretproject\n", said))
        self.assertIn("訊息過不了個資掃描", said[0])
        self.assertTrue(release.meta_scan(head, "Altention v0.1.0\n\n- first\n", []))
        self.assertEqual(git(self.private, "rev-parse", "HEAD"), head, "the probe never moves the branch")
        self.assertEqual(git(self.private, "status", "--porcelain"), "")

    def test_settings_missing(self):
        for rel, says in ((None, "沒有 release 設定"), ({"author": AUTHOR}, "release.repo"),
                          ({"repo": self.public, "author": "no address"}, "release.author"),
                          ({"repo": self.public, "author": "Alice <alice@example.com>"}, "noreply")):
            with self.subTest(says=says):
                self.settings(rel)
                code, out = self.run_("check")
                self.assertEqual(code, 2, out)
                self.assertIn(says, out)

    def origin_tag(self, name):
        p = subprocess.run(["git", "-C", self.origin, "rev-parse", "-q", "--verify", "refs/tags/%s^{commit}" % name],
                           stdout=subprocess.PIPE, universal_newlines=True)
        return p.stdout.strip()

    def test_the_tag_goes_on_this_repositorys_own_commit_too(self):
        """C-55: a machine following this repository (not the public one) sees the release as a tag on the commit
        whose tree was published; the dry run only says it will."""
        head = git(self.private, "rev-parse", "HEAD")
        code, out = self.run_("publish", "0.1.0")
        self.assertEqual(code, 0, out)
        self.assertIn("推的時候也會在這個 repo 的 remote origin 把 v0.1.0 打在 %s" % head[:12], out)
        self.assertEqual(self.origin_tag("v0.1.0"), "", "the dry run pushes nothing")
        code, out = self.run_("publish", "0.1.0", "--push")
        self.assertEqual(code, 0, out)
        self.assertEqual(self.origin_tag("v0.1.0"), head, "this repository's commit, not the public one")
        self.assertNotEqual(self.public_main(), head)
        self.assertIn("也打了 v0.1.0", out)

    def test_the_upstream_remote_of_the_branch_is_the_one(self):
        other = os.path.join(self.tmp, "other origin.git")
        git(self.tmp, "init", "-q", "--bare", other)
        git(self.private, "remote", "add", "mine", other)
        git(self.private, "push", "-q", "-u", "mine", "main")
        self.assertEqual(self.run_("publish", "0.1.0", "--push")[0], 0)
        self.assertEqual(git(other, "rev-parse", "v0.1.0^{commit}"), git(self.private, "rev-parse", "HEAD"))
        self.assertEqual(self.origin_tag("v0.1.0"), "")

    def test_a_tag_already_there_on_this_commit_is_left_on_another_refused(self):
        head = git(self.private, "rev-parse", "HEAD")
        git(self.private, "push", "-q", "origin", "%s:refs/tags/v0.1.0" % head)  # tagged by hand, as the three before
        code, out = self.run_("publish", "0.1.0", "--push")
        self.assertEqual(code, 0, out)
        self.assertIn("已經有 v0.1.0 在 %s，不用再打" % head[:12], out)
        self.write("tool.py", "X = 4  %s old place, until after the release\n" % HASH)
        self.commit("four")
        git(self.private, "push", "-q", "origin", "%s:refs/tags/v0.2.0" % head)
        public = self.public_main()
        code, out = self.run_("publish", "0.2.0", "--push")
        self.assertEqual(code, 1, out)
        self.assertIn("已經有 tag v0.2.0，在 %s" % head[:12], out)
        self.assertEqual(self.public_main(), public, "refused before anything was made")
        self.assertEqual(self.origin_tag("v0.2.0"), head, "a tag never moves")

    def test_this_repositorys_tag_refused_after_the_public_push_says_how_to_push_it(self):
        hook = os.path.join(self.origin, "hooks", "update")
        with open(hook, "w") as f:
            f.write('#!/bin/sh\ncase "$1" in refs/tags/*) echo "no tags here" >&2; exit 1;; esac\nexit 0\n')
        os.chmod(hook, 0o755)
        head = git(self.private, "rev-parse", "HEAD")
        code, out = self.run_("publish", "0.1.0", "--push")
        self.assertEqual(code, 2, out)
        self.assertTrue(self.public_main(), "the public release went out")
        self.assertIn("補推：git push origin %s:refs/tags/v0.1.0" % head, out)

    def test_no_remote_of_its_own_nothing_made(self):
        git(self.private, "remote", "remove", "origin")
        code, out = self.run_("publish", "0.1.0", "--push")
        self.assertEqual(code, 2, out)
        self.assertIn("找不到這個 repo 自己的 remote", out)
        self.assertEqual(self.public_main(), "")
        self.assertEqual(self.run_("check")[0], 0, "check does not need it")

    def test_usage(self):
        for argv in ([], ["push"], ["check", "--push"], ["publish"], ["publish", "1", "2", "3"]):
            self.assertEqual(self.run_(*argv)[0], 2, argv)


if __name__ == "__main__":
    unittest.main()
