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
import re
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

    def test_a_release_without_its_own_words_is_reminded_not_refused(self):
        """C-57 U8: no "### For people (zh-TW)" (or one without a point): a reminder; published all the same."""
        code, out = self.run_("publish", "0.1.0")
        self.assertEqual(code, 0, out)
        self.assertIn("提醒：CHANGELOG.md 的 v0.1.0 沒有「### For people (zh-TW)」這段", out)
        for part, said in (("### For people (zh-TW)\n\n", True), ("### For people (zh-tw)\n\n- 新的\n", False)):
            with self.subTest(part=part):
                self.write("CHANGELOG.md", CHANGES.replace("- first\n- 中文 too\n", "### For people\n\n- first\n\n" + part))
                self.commit("words " + part)
                code, out = self.run_("publish", "0.1.0")
                self.assertEqual(code, 0, out)
                self.assertEqual("For people (zh-TW)」這段" in out, said, out)

    def test_english_is_not_asked_for_its_own_words(self):
        """C-70: once altention/text.py speaks English, plain "For people" is its part: no reminder for en."""
        with mock.patch.dict(release.alt_text.TEXT, {"en": {}, "en-GB": {}}):
            code, out = self.run_("publish", "0.1.0")
        self.assertEqual(code, 0, out)
        self.assertNotIn("For people (en", out)
        self.assertIn("For people (zh-TW)」這段", out, "the others still are")

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



PLUGIN = {"name": "demo", "title": "Demo", "description": "a plugin for the tests"}


class Plugins(Rig):
    """publish-plugin: an external plugin's own commits, to its own public repository, one commit a release."""
    def setUp(self):
        super().setUp()
        self.root = os.path.join(self.tmp, "根 root")
        self.plug = os.path.join(self.root, "plugins", "demo")  # its folder, a repository of its own (C-61)
        os.makedirs(self.plug)
        git(self.plug, "init", "-q")
        git(self.plug, "checkout", "-q", "-b", "main")
        self.put("plugin.json", json.dumps(PLUGIN))
        self.put("CHANGELOG.md", CHANGES)
        self.put("bin/run", "X = 1  %s not the engine's register\n" % HASH)  # COMPAT is the engine's matter
        self.plug_commit("one")
        self.plug_origin = os.path.join(self.tmp, "plugin private.git")
        git(self.tmp, "init", "-q", "--bare", self.plug_origin)
        git(self.plug, "remote", "add", "origin", self.plug_origin)
        git(self.plug, "push", "-q", "-u", "origin", "main")
        self.plug_public = os.path.join(self.tmp, "plugin public.git")
        git(self.tmp, "init", "-q", "--bare", self.plug_public)
        git(self.plug_public, "symbolic-ref", "HEAD", "refs/heads/main")
        self.settings({"repo": self.public, "author": AUTHOR, "plugins": {"demo": {"repo": self.plug_public}}})
        self.root_env = mock.patch.dict(os.environ, {"ALT_ROOT": self.root})
        self.root_env.start()

    def tearDown(self):
        self.root_env.stop()
        super().tearDown()

    def put(self, rel, text):
        path = os.path.join(self.plug, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)

    def plug_commit(self, msg):
        git(self.plug, "add", "-A")
        git(self.plug, "commit", "-q", "-m", msg)
        return git(self.plug, "rev-parse", "HEAD")

    def plug_main(self):
        return subprocess.run(["git", "-C", self.plug_public, "rev-parse", "-q", "--verify", "refs/heads/main"],
                              stdout=subprocess.PIPE, universal_newlines=True).stdout.strip()

    def test_check_then_one_public_commit_a_release_tagged_on_both(self):
        code, out = self.run_("check-plugin", "demo")
        self.assertEqual(code, 0, out)
        self.assertIn("plugin.json", out)
        head = git(self.plug, "rev-parse", "HEAD")
        code, out = self.run_("publish-plugin", "demo", "0.1.0")
        self.assertEqual(code, 0, out)
        self.assertEqual(self.plug_main(), "", "not pushed without --push")
        code, out = self.run_("publish-plugin", "demo", "0.1.0", "--push")
        self.assertEqual(code, 0, out)
        first = self.plug_main()
        shown = git(self.plug_public, "show", "-s", "--format=%T|%P|%an <%ae>|%cn <%ce>|%s", first).split("|")
        self.assertEqual(shown, [git(self.plug, "rev-parse", head + "^{tree}"), "", AUTHOR, AUTHOR, "demo v0.1.0"])
        self.assertEqual(git(self.plug_public, "rev-parse", "v0.1.0^{commit}"), first)
        self.assertEqual(git(self.plug_origin, "rev-parse", "v0.1.0^{commit}"), head, "its own repo: its own commit")
        self.assertIn("別人看得到", out)
        self.assertEqual(self.public_main(), "", "the engine's public repo is not touched")
        self.assertIsNone(release.REPO)
        self.put("bin/run", "X = 2\n")
        second_head = self.plug_commit("two")
        code, out = self.run_("publish-plugin", "demo", "0.2.0", second_head, "--push")
        self.assertEqual(code, 0, out)
        second = self.plug_main()
        self.assertEqual(git(self.plug_public, "show", "-s", "--format=%P", second), first, "one commit a release")
        self.assertEqual(git(self.plug_public, "rev-list", "--count", "main"), "2")

    def test_its_plugin_json_must_be_readable_and_its_own(self):
        for text, why in ((json.dumps(dict(PLUGIN, name="other")), "不是 'demo'"), ("{", "讀不懂"),
                          (json.dumps({"name": "demo"}), "讀不懂")):
            with self.subTest(why=why):
                self.put("plugin.json", text)
                self.plug_commit("bad")
                code, out = self.run_("publish-plugin", "demo", "0.1.0", "--push")
                self.assertEqual(code, 1, out)
                self.assertIn(why, out)
                self.assertEqual(self.plug_main(), "")
        git(self.plug, "rm", "-q", "plugin.json")
        git(self.plug, "commit", "-q", "-m", "gone")
        code, out = self.run_("check-plugin", "demo")
        self.assertEqual(code, 1, out)
        self.assertIn("沒有 plugin.json", out)

    def test_what_it_takes_from_the_engine_c94(self):
        """check-plugin reads the plugin's files (tests and docs aside): a dcbot/ file or altention module no
        interface lists is refused, an interface used but not in "uses" too; a transitional one is a reminder."""
        def check(files, uses=None):
            for rel, text in files.items():
                self.put(rel, text)
            self.put("plugin.json", json.dumps(dict(PLUGIN, **({"uses": uses} if uses is not None else {}))))
            self.plug_commit("try")
            code, out = self.run_("check-plugin", "demo")
            if files:
                git(self.plug, "rm", "-q", *files)
                self.plug_commit("clean")
            return code, out
        dcctl = {"bin/go.py": 'import os\nP = os.path.join(ENGINE, "dcbot", "tools", "dcctl")\n'}
        code, out = check(dcctl)
        self.assertEqual(code, 1, out)
        self.assertIn('拒絕：bin/go.py:2 用到介面 dcctl，plugin.json 的 uses 要寫 "dcctl": 1', out)
        code, out = check(dcctl, {"dcctl": 1})
        self.assertEqual(code, 0, out)
        code, out = check({"bin/go.sh": "python3 ~/altention/engine/dcbot/bot/main.py\n"}, {})
        self.assertEqual(code, 1, out)
        self.assertIn("拒絕：bin/go.sh:1 用到 dcbot/bot/main.py：不是公開的介面", out)
        code, out = check({"lib/m.py": "from altention import sync\n"}, {})
        self.assertEqual(code, 1, out)
        self.assertIn("拒絕：lib/m.py:1 import 了 altention.sync：altention 裡沒標公開的模組", out)
        code, out = check({"lib/m.py": "from altention import config\nconfig.engine_raw('x')\nconfig.setting('a')\n",
                           "lib/s.py": 'F = "dcbot/state/channels.json"\n'}, {"py.config": 1, "state.channels": 1})
        self.assertEqual(code, 0, out)
        self.assertIn("提醒：lib/m.py:2 用到 altention.config.engine_raw：altention.config 的其他函式不公開", out)
        self.assertNotIn("config.setting", out)
        self.assertIn("提醒：lib/s.py:1 用到 state.channels：之後的版本有正式的唯讀指令後會移除", out)
        code, out = check({"tests/test_x.py": "from altention import sync\n", "docs/a.md": "dcbot/bot/main.py\n",
                           "lib/d.py": '"""Reads dcbot/bot/main.py."""\n# dcbot/bot/x.py\nimport os\n'}, {})
        self.assertEqual(code, 0, "tests, docs, docstrings and comments are not what it runs: " + out)
        code, out = check({"lib/v.py": 'import os\nP = os.path.join(E, "dcbot", name)\n'}, {})
        self.assertEqual(code, 0, out)
        self.assertIn("提醒：lib/v.py:2 組了一個 dcbot 下的路徑（dcbot/…）", out)
        code, out = check({}, {"dc.post": 9})
        self.assertEqual(code, 1, out)
        self.assertIn("uses 在這版引擎對不上（裝了也不會啟用）：它需要 dc.post v9", out)

    def test_more_ways_a_plugin_reaches_engine_files_c94(self):
        """After #code-review(-2) of 8742d70: a file that is not text (a font) is skipped, not a crash; a path put
        together with pathlib's / counts; a folder joined first and a file's name later counts as that file; a skill
        that tells Claude to run an engine file is a reminder."""
        files = {"site/fonts/a.woff2": b"wOF2\x00\x01\xff\xfe\x80" * 50,
                 "lib/p.py": 'from pathlib import Path\nX = Path(E) / "dcbot" / "bot" / "core.py"\n',
                 "lib/w.py": 'import os\nS = os.path.join(E, "dcbot", "state")\nW = os.path.join(S, "waiting-on-owner.json")\n',
                 "skills/rig/SKILL.md": "Run `python3 ~/altention/engine/ops/apply.py` and `bin/run`.\n",
                 "bin/run": "x\n"}
        for rel, content in files.items():
            path = os.path.join(self.plug, rel)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "wb") as f:
                f.write(content if isinstance(content, bytes) else content.encode("utf-8"))
        self.put("plugin.json", json.dumps(dict(PLUGIN, uses={})))
        self.plug_commit("more")
        code, out = self.run_("check-plugin", "demo")
        self.assertNotIn("Traceback", out)
        self.assertEqual(code, 1, out)
        self.assertIn("拒絕：lib/p.py:2 用到 dcbot/bot/core.py：不是公開的介面", out)
        self.assertIn('拒絕：lib/w.py:3 用到介面 state.waiting，plugin.json 的 uses 要寫 "state.waiting": 1', out)
        self.assertIn("提醒：skills/rig/SKILL.md:1 叫 Claude 跑 ops/apply.py：skill 也要走公開的介面", out)
        self.assertNotIn("bin/run", out.replace("提醒：", ""), "the plugin's own files are its own")
        self.assertNotIn("woff2", out)

    def test_uses_the_words_show_must_be_written_c94(self):
        """#display-review (manager 2026-10-24): uses that the plugin's own words show are refused when not written:
        rewrite and activity.pages from plugin.json, a {token} in plugin.json or $DCBOT_CHANNEL, altention dc
        post/notify/read in code, scripts or a skill. Comments and docstrings do not count."""
        files = {"bin/send.py": 'import subprocess\nsubprocess.run([ALT, "dc", "notify", "x"], timeout=60)\n'
                                '# altention dc read here is only a comment\n',
                 "bin/run.sh": "altention dc post @audit hi\n",
                 "skills/rig/SKILL.md": "Look it up with `altention dc read --map`; post to \"$DCBOT_CHANNEL\".\n"}
        for rel, text in files.items():
            self.put(rel, text)
        d = dict(PLUGIN, rewrite={"command": "{python} x.py"}, activity={"pages": [{"id": "home"}]})
        self.put("plugin.json", json.dumps(d, indent=1))
        self.plug_commit("words")
        code, out = self.run_("check-plugin", "demo")
        self.assertEqual(code, 1, out)
        refused = sorted(re.findall(r'uses 要寫 "([a-z.]+)"', out))
        self.assertEqual(refused, ["activity.pages", "dc.notify", "dc.post", "dc.read", "rewrite", "tokens"], out)
        self.assertIn("拒絕：bin/send.py:2 用到介面 dc.notify", out)
        self.assertIn("拒絕：skills/rig/SKILL.md:1 用到介面 dc.read", out, "the skill, not the comment in send.py")
        self.put("plugin.json", json.dumps(dict(d, uses={n: 1 for n in refused}), indent=1))
        self.plug_commit("written")
        code, out = self.run_("check-plugin", "demo")
        self.assertEqual(code, 0, out)

    def test_an_interface_of_its_own_may_not_take_an_engine_name(self):
        self.put("plugin.json", json.dumps(dict(PLUGIN, interfaces={"dc.post": {"version": 1}, "rig.spend": {"version": 1}})))
        self.plug_commit("offers")
        code, out = self.run_("check-plugin", "demo")
        self.assertEqual(code, 1, out)
        self.assertIn("拒絕：plugin.json 宣告的介面 dc.post 跟引擎的重名，請改名", out)
        self.assertNotIn("rig.spend", out)

    def test_keys_the_engine_no_longer_reads_are_a_reminder_not_a_refusal_c95(self):
        self.put("plugin.json", json.dumps(dict(PLUGIN, commands=[{"name": "x", "path": "x"}],
                                                requires=[{"plugin": "chatlog", "data": "chatlog"}])))
        self.plug_commit("old keys")
        code, out = self.run_("check-plugin", "demo")
        self.assertEqual(code, 0, out)
        self.assertIn("提醒：plugin.json 還寫著 commands，引擎已經不讀了", out)
        self.assertIn("提醒：plugin.json 還寫著 requires[].data", out)
        self.assertNotIn("拒絕", out)

    def test_something_personal_in_the_plugin_or_its_message(self):
        self.put("notes.txt", "for secretproject\n")
        self.plug_commit("personal")
        code, out = self.run_("publish-plugin", "demo", "0.1.0", "--push")
        self.assertEqual(code, 1, out)
        self.assertIn("個資掃描找到東西", out)
        git(self.plug, "rm", "-q", "notes.txt")
        head = self.plug_commit("clean again")
        said = []
        with mock.patch.object(release, "REPO", self.plug):  # the message's own scan looks in the plugin's repo
            self.assertFalse(release.meta_scan(head, "demo v0.1.0\n\n- about secretproject\n", said))
            self.assertTrue(release.meta_scan(head, "demo v0.1.0\n\n- first\n", []))
        self.assertIn("訊息過不了個資掃描", said[0])
        self.assertEqual(git(self.plug, "rev-parse", "HEAD"), head)
        self.assertEqual(self.plug_main(), "")

    def test_no_public_repo_set_or_no_repository_nothing_made(self):
        self.settings({"repo": self.public, "author": AUTHOR})
        code, out = self.run_("publish-plugin", "demo", "0.1.0", "--push")
        self.assertEqual(code, 2, out)
        self.assertIn("release.plugins.demo.repo", out)
        self.settings({"repo": self.public, "author": AUTHOR, "plugins": {"gone": {"repo": self.plug_public}}})
        code, out = self.run_("check-plugin", "gone")
        self.assertEqual(code, 2, out)
        self.assertIn("不是 git repo", out)
        self.assertIsNone(release.REPO)

    def test_its_own_public_address_passes_the_scan_a_private_one_does_not(self):
        """The plugin's README names its public repository: that is public by the settings alone (privacy-scan reads
        release.plugins), no --public-repo typed by hand; another repository of the same owner is still found."""
        org = "nightjar"
        public = "git@github.com:%s/plugin-demo.git" % org
        self.settings({"repo": self.public, "author": AUTHOR, "plugins": {"demo": {"repo": public}}})
        with mock.patch.object(release, "fetch_public", return_value=("", {})):
            self.put("README.md", "git clone https://github.com/%s/plugin-demo.git\nsee github.com/%s/plugin-demo.\n" % (org, org))
            self.plug_commit("readme")
            code, out = self.run_("check-plugin", "demo")
            self.assertEqual(code, 0, out)
            self.put("README.md", "git clone https://github.com/%s/plugin-demo-private.git\n" % org)
            self.plug_commit("private named")
            code, out = self.run_("check-plugin", "demo")
            self.assertEqual(code, 1, out)
            self.assertIn("github-owner", out)

    def test_a_public_repo_nobody_can_read_is_said(self):
        with mock.patch.object(release, "anonymous_url", return_value=os.path.join(self.tmp, "not there.git")):
            code, out = self.run_("publish-plugin", "demo", "0.1.0", "--push")
        self.assertEqual(code, 2, out)
        self.assertIn("推上去了，但沒登入讀不到", out)
        self.assertTrue(self.plug_main(), "the push itself went out")

    def test_reading_as_anyone_uses_an_empty_home(self):
        """No ~/.netrc (libcurl reads it whatever git's settings say), ~/.gitconfig or XDG file of this machine's:
        the read runs with HOME an empty folder of its own (#code-review)."""
        with open(os.path.join(os.environ["HOME"], ".netrc"), "w") as f:
            f.write("machine example.com login someone password x\n")
        seen, real = [], release.git

        def spy(*args, **kw):
            if args[:1] == ("fetch",):
                home = kw["env"]["HOME"]
                seen.append((home, os.listdir(home), kw["env"]["XDG_CONFIG_HOME"].startswith(home)))
            return real(*args, **kw)
        with mock.patch.object(release, "git", spy):
            code, out = self.run_("publish-plugin", "demo", "0.1.0", "--push")
        self.assertEqual(code, 0, out)
        self.assertEqual(len(seen), 1)
        home, inside, xdg = seen[0]
        self.assertNotEqual(home, os.environ["HOME"])
        self.assertEqual((inside, xdg), ([], True))
        self.assertFalse(os.path.exists(home), "removed afterwards")

    def test_seen_again_later(self):
        """After a publish whose repository could not be read yet, seen-plugin reads it again; nothing is made."""
        with mock.patch.object(release, "anonymous_url", return_value=os.path.join(self.tmp, "not there.git")):
            code, out = self.run_("publish-plugin", "demo", "0.1.0", "--push")
        self.assertEqual(code, 2, out)
        self.assertIn("python3 ops/release.py seen-plugin demo 0.1.0", out)
        main = self.plug_main()
        code, out = self.run_("seen-plugin", "demo", "0.1.0")
        self.assertEqual(code, 0, out)
        self.assertIn("別人看得到", out)
        self.assertEqual(self.plug_main(), main)
        self.assertEqual(self.run_("seen-plugin", "demo", "0.2.0")[0], 2, "no such tag here or there")
        self.put("bin/run", "X = 3\n")
        other = self.plug_commit("not published")
        git(self.plug, "push", "-q", "origin", "%s:refs/tags/v0.3.0" % other)
        git(self.plug_public, "tag", "v0.3.0", main)  # a tag on the public repo with another tree
        code, out = self.run_("seen-plugin", "demo", "0.3.0")
        self.assertEqual(code, 2, out)
        self.assertIn("不是這裡 v0.3.0 的 tree", out)
        for argv in (["seen-plugin", "demo"], ["seen-plugin", "demo", "x.y"], ["seen-plugin", "core", "0.1.0"],
                     ["seen-plugin", "demo", "0.1.0", "--push"]):
            self.assertEqual(self.run_(*argv)[0], 2, argv)
        self.assertIsNone(release.REPO)

    def test_the_address_anyone_reads(self):
        for given, seen in (("git@github.com:example/plugin-demo.git", "https://github.com/example/plugin-demo.git"),
                            ("ssh://git@github.com/example/plugin-demo", "https://github.com/example/plugin-demo"),
                            ("https://github.com/example/plugin-demo.git", "https://github.com/example/plugin-demo.git"),
                            ("/srv/git/demo.git", "/srv/git/demo.git")):
            self.assertEqual(release.anonymous_url(given), seen)

    def test_usage(self):
        for argv in (["check-plugin"], ["publish-plugin", "demo"], ["publish-plugin", "Bad_Name", "0.1.0"],
                     ["check-plugin", "core"], ["check-plugin", "demo", "a", "b"], ["publish-plugin", "demo", "1", "2", "3"]):
            self.assertEqual(self.run_(*argv)[0], 2, argv)


if __name__ == "__main__":
    unittest.main()
