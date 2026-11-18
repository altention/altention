"""C-148: tools/golden and altention.testing.golden. Tests only read golden files; the tool is the only writer and
takes cases one by one; a commit changing a golden file names its cases in a "golden:" line, which tools/lint checks
(--against-rev / --against-upstream)."""
import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))))
from altention import testing as _testing  # noqa: E402
_testing.guard_home()  # tests never touch the real home (altention/testing.py)
import contextlib
import importlib.machinery
import importlib.util
import io
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

from altention import testing

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.join(os.path.dirname(HERE), "golden")
loader = importlib.machinery.SourceFileLoader("golden_tool", TOOL)
golden = importlib.util.module_from_spec(importlib.util.spec_from_loader("golden_tool", loader))
loader.exec_module(golden)


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


class Helper(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="golden-helper-")
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_compares_and_never_writes(self):
        write(os.path.join(self.tmp, "golden", "cli", "x.txt"), "#golden spec §1\na\nb\n")
        testing.golden("cli/x", "a\nb\n", "§1", folder=self.tmp)
        with self.assertRaises(AssertionError) as e:
            testing.golden("cli/x", "a\nc\n", "§1", folder=self.tmp)
        self.assertIn("-b\n+c", str(e.exception))
        self.assertIn("tools/golden update cli/x", str(e.exception))
        self.assertIn("golden: cli/x — <為什麼>", str(e.exception))
        with open(os.path.join(self.tmp, "golden", "cli", "x.txt"), encoding="utf-8") as f:
            self.assertEqual(f.read(), "#golden spec §1\na\nb\n", "a difference never rewrites the file")

    def test_none_yet_fails_saying_how(self):
        with self.assertRaises(AssertionError) as e:
            testing.golden("cli/new", {"a": 1}, "§2", folder=self.tmp)
        self.assertIn("golden cli/new 還沒有（spec §2）", str(e.exception))
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "golden")))

    def test_what_it_saw_is_left_only_for_the_tool(self):
        out = os.path.join(self.tmp, "out")
        with mock.patch.dict(os.environ, {"ALT_GOLDEN_OUT": out}), self.assertRaises(AssertionError):
            testing.golden("cli/new", {"b": [1, "x"]}, "§2", folder=self.tmp)
        with open(os.path.join(out, "cli__new"), encoding="utf-8") as f:
            got = json.load(f)
        self.assertEqual((got["case"], got["ext"], got["spec"]), ("cli/new", ".json", "§2"))
        self.assertEqual(json.loads(got["text"]), {"b": [1, "x"]})
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "golden")))

    def test_the_home_and_the_engine_read_as_words(self):
        engine = os.path.dirname(os.path.dirname(HERE))
        text = testing.golden_text("%s/x %s/y\n" % (os.environ["HOME"], engine))
        self.assertEqual(text, "<HOME>/x <ENGINE>/y\n")

    def test_shape(self):
        self.assertEqual(testing.shape({"v": 2, "a": [1, 2.5, "x", None, True], "b": {"c": []}}, keep=("v",)),
                         {"v": 2, "a": ["number", "str", "null", "bool"], "b": {"c": []}})


class Rig(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="golden-repo-")
        self.addCleanup(shutil.rmtree, self.root, True)
        self.git("init", "-q")
        write(os.path.join(self.root, "a.txt"), "a\n")
        self.commit("start")
        p = mock.patch.object(golden, "ENGINE", self.root)
        p.start()
        self.addCleanup(p.stop)

    def git(self, *args):
        return subprocess.run(["git", "-C", self.root, "-c", "user.name=rig", "-c", "user.email=rig@example.com"] + list(args),
                              stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, universal_newlines=True, timeout=60, check=True)

    def commit(self, message):
        self.git("add", "-A")
        self.git("commit", "-q", "-m", message)
        return self.git("rev-parse", "HEAD").stdout.strip()

    def run_tool(self, *argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = golden.main(list(argv))
        return code, out.getvalue()


class Commits(Rig):
    def test_a_commit_changing_golden_names_exactly_its_cases(self):
        base = self.git("rev-parse", "HEAD").stdout.strip()
        write(os.path.join(self.root, "ops", "tests", "golden", "cli", "x.txt"), "x\n")
        write(os.path.join(self.root, "dcbot", "tests", "golden", "discord", "ids.json"), "{}\n")
        self.commit("change two\n\ngolden: cli/x, discord/ids — the ids grew\n")
        self.assertEqual(self.run_tool("check-commits", base)[0], 0)
        self.assertEqual(self.run_tool("changed", base)[1], "cli/x\ndiscord/ids\n")
        write(os.path.join(self.root, "ops", "tests", "golden", "cli", "x.txt"), "y\n")
        bad = self.commit("no line")
        code, out = self.run_tool("check-commits", base)
        self.assertEqual(code, 1)
        self.assertIn("%s 改了 golden cli/x，但 commit 訊息的 golden: 那一行寫的是（沒有這一行）" % bad[:12], out)
        write(os.path.join(self.root, "ops", "tests", "golden", "cli", "x.txt"), "z\n")
        self.commit("wrong one\n\ngolden: cli/y — oops\n")
        code, out = self.run_tool("check-commits", bad)
        self.assertEqual(code, 1)
        self.assertIn("寫的是 cli/y", out)
        write(os.path.join(self.root, "a.txt"), "b\n")
        self.assertEqual(self.run_tool("check-commits", self.commit("no golden"))[0], 0)

    def test_a_merge_is_not_checked(self):
        base = self.git("rev-parse", "HEAD").stdout.strip()
        self.git("checkout", "-q", "-b", "side")
        write(os.path.join(self.root, "ops", "tests", "golden", "cli", "x.txt"), "x\n")
        self.commit("side\n\ngolden: cli/x — new\n")
        self.git("checkout", "-q", "-")
        write(os.path.join(self.root, "b.txt"), "b\n")
        self.commit("main")
        self.git("-c", "user.name=rig", "-c", "user.email=rig@example.com", "merge", "-q", "--no-edit", "side")
        self.assertEqual(self.run_tool("check-commits", base)[0], 0, "the merge brings cli/x without a line: fine")

    def test_update_takes_cases_one_by_one(self):
        for argv in ([], ["all"], ["cli/*"], ["cli/x", "../y"]):
            code, out = self.run_tool("update", *argv)
            self.assertEqual(code, 2, argv)
            self.assertIn("要一個一個寫出情況", out)
        self.assertEqual(self.run_tool("update", "cli/none-such")[0], 1)

    def test_write_keeps_the_header(self):
        folder = os.path.join(self.root, "ops", "tests")
        write(os.path.join(folder, "golden", "cli", "x.txt"), "#golden spec §9\nold\n")
        with contextlib.redirect_stdout(io.StringIO()):
            golden.write({"case": "cli/x", "folder": folder, "spec": "§1", "ext": ".txt", "text": "new\n"})
            golden.write({"case": "cli/y", "folder": folder, "spec": "§2", "ext": ".json", "text": "{}\n"})
        with open(os.path.join(folder, "golden", "cli", "x.txt"), encoding="utf-8") as f:
            self.assertEqual(f.read(), "#golden spec §9\nnew\n")
        with open(os.path.join(folder, "golden", "cli", "y.json"), encoding="utf-8") as f:
            self.assertEqual(f.read(), "#golden spec §2\n{}\n")

    def test_changed_says_its_errors_on_stderr(self):
        """#code-review-2 of d45add2: deploy.sh prints changed's stdout as the cases; an error is not one."""
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            code, out = self.run_tool("changed", "no-such-rev")
        self.assertEqual((code, out), (2, ""))
        self.assertIn("golden changed：", err.getvalue())

    def test_deploy_sh_joins_the_cases_with_a_whole_character(self):
        """#code-review-2 of d45add2: tr cut 「、」 to one byte; deploy.sh's line is valid UTF-8 with several cases."""
        with open(os.path.join(os.path.dirname(os.path.dirname(HERE)), "dcbot", "deploy.sh"), encoding="utf-8") as f:
            line = next(l for l in f if "GOLDEN_CHANGED=\"$(" in l)
        joiner = line.split("2>/dev/null", 1)[1].split("|| true", 1)[0].strip().lstrip("|").strip()
        p = subprocess.run(["bash", "-c", joiner], input="cli/a\ncli/b\ndiscord/c\n".encode(), stdout=subprocess.PIPE, timeout=30)
        self.assertEqual(p.stdout.decode("utf-8").strip(), "cli/a、cli/b、discord/c")

    def test_a_file_gitignore_leaves_out_is_said(self):
        """C-148 batch 2: dcbot's .gitignore leaves out every folder named state/; a golden file there never got in."""
        write(os.path.join(self.root, ".gitignore"), "state/\n")
        folder = os.path.join(self.root, "dcbot", "tests")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertFalse(golden.write({"case": "state/x", "folder": folder, "spec": "§1", "ext": ".txt", "text": "x\n"}))
            self.assertTrue(golden.write({"case": "bot-state/x", "folder": folder, "spec": "§1", "ext": ".txt", "text": "x\n"}))
        self.assertIn("被 .gitignore 略過，不會進 commit", out.getvalue())

    def test_help_and_wrong_usage(self):
        self.assertEqual(self.run_tool("--help")[0], 0)
        self.assertEqual(self.run_tool("nope")[0], 2)


if __name__ == "__main__":
    unittest.main()
