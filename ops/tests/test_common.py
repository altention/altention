"""ops/: the machine list through altention.config; no list is one machine marked "single"."""
import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))))
from altention import testing as _testing  # noqa: E402
_testing.guard_home()  # tests never touch the real home (altention/testing.py)
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import common  # noqa: E402


class Inventory(unittest.TestCase):
    def test_no_list_is_this_machine_alone_and_marked_single(self):
        with mock.patch.object(common.altconfig, "inventory", return_value={}), \
                mock.patch.object(common.altconfig, "this_machine", return_value="box"):
            inv = common.inventory()
        self.assertTrue(inv["single"])
        self.assertEqual(list(inv["machines"]), ["box"])

    def test_a_list_is_used_as_it_is(self):
        data = {"machines": {"a": {"ssh": ["A"], "roles": ["chatlog"]}}}
        with mock.patch.object(common.altconfig, "inventory", return_value=data):
            inv = common.inventory()
        self.assertNotIn("single", inv)
        self.assertEqual(inv["machines"], data["machines"])

    def test_apply_refuses_without_a_list(self):
        import subprocess
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        env = dict(os.environ, ALT_INSTANCE="", HOME="/nonexistent-home")
        r = subprocess.run([sys.executable, "-c", "import sys; sys.argv=['apply','all']; sys.path.insert(0,'%s');"
                            "import common; common.altconfig.ENGINE='/nonexistent'; import apply; sys.exit(apply.main())" % here],
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, env=env, cwd=here)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("沒有機器清單", r.stdout + r.stderr)
        self.assertIn("python3 altention/update.py --release", r.stdout, "says how one machine is updated instead")
        self.assertIn("./install.sh", r.stdout)



class RunOn(unittest.TestCase):
    def test_a_command_reading_stdin_does_not_swallow_the_rest_of_the_script(self):
        script = "echo before\ncat >/dev/null\nread x || echo stdin-is-empty\necho after\n" + "# padding\n" * 2000
        alias, code, out, err = common.run_on({"ssh": ["local"]}, script, interpreter="sh")
        self.assertEqual((alias, code), ("local", 0), err)
        self.assertEqual(out.split(), ["before", "stdin-is-empty", "after"])

    def test_quotes_dollars_and_chinese_survive(self):
        script = "x='a \"b\" $HOME'; printf '%s|中文 空白\\n' \"$x\"\n"
        alias, code, out, err = common.run_on({"ssh": ["local"]}, script, interpreter="sh")
        self.assertEqual(out, 'a "b" $HOME|中文 空白\n')



class LocalHead(unittest.TestCase):
    def test_an_engine_without_git_says_nothing_and_has_no_head(self):
        """An engine unpacked from an archive: "" and nothing from git on stderr (doctor's first line was git's
        "fatal: not a git repository"; #code-review-2 2026-10-14)."""
        import subprocess
        import tempfile
        top = tempfile.mkdtemp(prefix="解開 engine ")
        try:
            os.makedirs(os.path.join(top, "ops"))
            code = ("import sys; sys.path.insert(0, %r); import common; common.HERE = %r; print(repr(common.local_head()))"
                    % (os.path.dirname(os.path.dirname(os.path.abspath(__file__))), os.path.join(top, "ops")))
            r = subprocess.run([sys.executable, "-c", code], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               universal_newlines=True, env=dict(os.environ, GIT_CEILING_DIRECTORIES=top))
            self.assertEqual((r.stdout.strip(), r.stderr), ("''", ""))
        finally:
            import shutil
            shutil.rmtree(top, ignore_errors=True)

    def test_a_checkout_has_its_head(self):
        """A checkout of its own in a throwaway folder: the tree the tests run in may well be unpacked (#code-review)."""
        import subprocess
        import tempfile
        import shutil
        top = tempfile.mkdtemp(prefix="clone engine ")
        try:
            os.makedirs(os.path.join(top, "ops"))
            for args in (["init", "-q"], ["commit", "-q", "--allow-empty", "-m", "x"]):
                subprocess.run(["git", "-C", top, "-c", "user.name=alice", "-c", "user.email=alice@example.com"] + args,
                               check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            with mock.patch.object(common, "HERE", os.path.join(top, "ops")):
                head, why = common.local_head_status()
                self.assertRegex(head, r"^[0-9a-f]{7,}$")
                self.assertEqual(why, "")
                self.assertEqual(common.local_head(), head)
        finally:
            shutil.rmtree(top, ignore_errors=True)

    def test_a_git_that_fails_is_never_taken_for_an_archive(self):
        """.git there, git failing (broken .git, no git, dubious ownership): the reason, not "unpacked"."""
        import tempfile
        import shutil
        top = tempfile.mkdtemp(prefix="壞掉 engine ")
        try:
            os.makedirs(os.path.join(top, "ops"))
            os.makedirs(os.path.join(top, ".git"))  # a .git that is no repository
            with mock.patch.object(common, "HERE", os.path.join(top, "ops")), \
                    mock.patch.dict(os.environ, {"GIT_CEILING_DIRECTORIES": top}):
                head, why = common.local_head_status()
                self.assertEqual(head, "")
                self.assertTrue(why.startswith("讀不到 git："), why)
                with mock.patch.object(common.subprocess, "run", side_effect=FileNotFoundError("git")):
                    self.assertTrue(common.local_head_status()[1].startswith("讀不到 git："))
            shutil.rmtree(os.path.join(top, ".git"))
            with mock.patch.object(common, "HERE", os.path.join(top, "ops")):
                self.assertEqual(common.local_head_status(), ("", ""), "no .git at all: unpacked, nothing wrong")
        finally:
            shutil.rmtree(top, ignore_errors=True)

if __name__ == "__main__":
    unittest.main()
