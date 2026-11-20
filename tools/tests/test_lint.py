"""tools/lint: the baseline only shrinks, a moved line is not new, a changed one is, ALT001, C901 only counted,
no ruff or another version, and which files are checked.

ruff is a stand-in printing what a test gives it, in ruff 0.17.0's own JSON shape (--output-format json)."""
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

HERE = os.path.dirname(os.path.abspath(__file__))
loader = importlib.machinery.SourceFileLoader("lint_tool", os.path.join(os.path.dirname(HERE), "lint"))
spec = importlib.util.spec_from_loader("lint_tool", loader)
lint = importlib.util.module_from_spec(spec)
loader.exec_module(lint)

# Prints FAKE_RUFF_VERSION for --version; for check, the rows in FAKE_RUFF_ROWS ({file, code, row}) whose file
# was asked for, shaped as ruff 0.17.0 prints them (copied from a real run), and the arguments into FAKE_RUFF_ARGS.
FAKE = r'''#!PYTHON
import json, os, sys
if sys.argv[1:] == ["--version"]:
    print("ruff " + os.environ.get("FAKE_RUFF_VERSION", "0.17.0"))
    sys.exit(0)
with open(os.environ["FAKE_RUFF_ARGS"], "w") as f:
    json.dump(sys.argv[1:], f)
asked = sys.argv[sys.argv.index("--") + 1:]
rows = json.load(open(os.environ["FAKE_RUFF_ROWS"])) if os.environ.get("FAKE_RUFF_ROWS") else []
out = []
for r in rows:
    if r["file"] in asked:
        loc = {"column": 1, "row": r["row"]}
        out.append({"cell": None, "code": r["code"], "end_location": loc, "filename": os.path.join(os.getcwd(), r["file"]),
                    "fix": None, "location": loc, "message": r.get("message", "msg"), "name": "x", "noqa_row": r["row"],
                    "severity": "error", "url": "https://docs.astral.sh/ruff/rules/x"})
print(json.dumps(out))
'''


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="lint-test-")
        self.root = os.path.join(self.tmp, "tree")
        os.makedirs(os.path.join(self.root, "tools"))
        self.ruff = os.path.join(self.tmp, "bin", "ruff")
        write(self.ruff, FAKE.replace("PYTHON", _sys.executable))
        os.chmod(self.ruff, 0o755)
        os.makedirs(os.path.join(self.tmp, "gitonly"))
        os.symlink(shutil.which("git"), os.path.join(self.tmp, "gitonly", "git"))
        self.env = mock.patch.dict(os.environ, {
            "ALT_RUFF": self.ruff, "FAKE_RUFF_ROWS": os.path.join(self.tmp, "rows.json"),
            "FAKE_RUFF_ARGS": os.path.join(self.tmp, "args.json"), "FAKE_RUFF_VERSION": lint.RUFF_VERSION,
            "PATH": os.path.join(self.tmp, "gitonly")})  # no ruff of this machine is ever found
        self.env.start()
        self.addCleanup(self.env.stop)
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def say(self, rows):
        with open(os.environ["FAKE_RUFF_ROWS"], "w") as f:
            json.dump(rows, f)

    def lint(self, *args):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = lint.main(["--root", self.root] + list(args))
        return code, out.getvalue()

    def baseline(self):
        with open(os.path.join(self.root, "tools", "lint-baseline.json"), encoding="utf-8") as f:
            return json.load(f)


class Baseline(Base):
    def setUp(self):
        super().setUp()
        write(os.path.join(self.root, "a.py"), "import os\nimport re\nx = 1\n")
        self.say([{"file": "a.py", "code": "F401", "row": 1}])

    def test_without_a_baseline_everything_must_pass(self):
        code, out = self.lint()
        self.assertEqual(code, lint.NEW, out)
        self.assertIn("a.py:1 F401", out)
        self.assertIn("沒有基準檔", out)

    def test_init_then_clean_and_init_never_again(self):
        self.assertEqual(self.lint("--init")[0], lint.OK)
        self.assertEqual(self.baseline()["items"], [{"count": 1, "file": "a.py", "line": "import os", "rule": "F401"}])
        self.assertEqual(self.baseline()["ruff"], lint.RUFF_VERSION)
        code, out = self.lint()
        self.assertEqual(code, lint.OK, out)
        code, out = self.lint("--init")
        self.assertEqual(code, lint.FAILED)
        self.assertIn("--shrink", out)

    def test_a_line_moved_down_is_not_new(self):
        self.lint("--init")
        write(os.path.join(self.root, "a.py"), "# added\n\nimport os\nimport re\nx = 1\n")
        self.say([{"file": "a.py", "code": "F401", "row": 3}])
        code, out = self.lint()
        self.assertEqual(code, lint.OK, out)

    def test_a_changed_line_must_pass(self):
        self.lint("--init")
        write(os.path.join(self.root, "a.py"), "import os, sys\nimport re\nx = 1\n")
        code, out = self.lint()
        self.assertEqual(code, lint.NEW, out)
        self.assertIn("a.py:1 F401", out)

    def test_one_more_of_the_same_line_is_new(self):
        self.lint("--init")
        write(os.path.join(self.root, "a.py"), "import os\nimport re\nimport os\n")
        self.say([{"file": "a.py", "code": "F401", "row": 1}, {"file": "a.py", "code": "F401", "row": 3}])
        code, out = self.lint()
        self.assertEqual(code, lint.NEW, out)
        self.assertIn("a.py:3 F401", out)
        self.assertNotIn("a.py:1 F401", out)

    def test_another_rule_on_a_held_line_is_new(self):
        self.lint("--init")
        self.say([{"file": "a.py", "code": "F401", "row": 1}, {"file": "a.py", "code": "F811", "row": 1}])
        code, out = self.lint()
        self.assertEqual(code, lint.NEW, out)
        self.assertIn("a.py:1 F811", out)

    def test_shrink_drops_what_is_fixed_and_never_adds(self):
        self.lint("--init")
        self.say([{"file": "a.py", "code": "F401", "row": 2}])  # the old one fixed, a new one in its place
        code, out = self.lint()
        self.assertEqual(code, lint.NEW)
        self.assertIn("可以從基準拿掉 1 處", out)
        self.assertIn("--shrink", out)
        self.assertEqual(self.lint("--shrink")[0], lint.OK)
        self.assertEqual(self.baseline()["items"], [])
        self.assertEqual(self.lint()[0], lint.NEW)  # the new one is still new

    def test_shrink_lowers_a_count(self):
        write(os.path.join(self.root, "a.py"), "import os\nimport os\n")
        self.say([{"file": "a.py", "code": "F401", "row": 1}, {"file": "a.py", "code": "F401", "row": 2}])
        self.lint("--init")
        self.assertEqual(self.baseline()["items"][0]["count"], 2)
        self.say([{"file": "a.py", "code": "F401", "row": 1}])
        self.lint("--shrink")
        self.assertEqual(self.baseline()["items"][0]["count"], 1)

    def test_an_item_marked_false_is_said(self):
        self.lint("--init")
        data = self.baseline()
        data["items"][0]["false"] = "used at once in the same loop"
        write(os.path.join(self.root, "tools", "lint-baseline.json"), json.dumps(data))
        code, out = self.lint()
        self.assertEqual(code, lint.OK)
        self.assertIn("1 處審查判為誤判", out)
        self.say([])
        self.lint("--shrink")
        self.assertEqual(self.baseline()["items"], [])

    def test_a_baseline_of_another_ruff_version_is_refused(self):
        self.lint("--init")
        data = self.baseline()
        data["ruff"] = "0.6.9"
        write(os.path.join(self.root, "tools", "lint-baseline.json"), json.dumps(data))
        code, out = self.lint()
        self.assertEqual(code, lint.FAILED)
        self.assertIn("0.6.9", out)

    def test_an_unreadable_baseline_is_a_failure_not_empty(self):
        write(os.path.join(self.root, "tools", "lint-baseline.json"), "{ not json")
        code, out = self.lint()
        self.assertEqual(code, lint.FAILED)
        self.assertIn("讀不出來", out)

    def test_complexity_is_counted_never_failed(self):
        self.say([{"file": "a.py", "code": "C901", "row": 3}])
        code, out = self.lint()
        self.assertEqual(code, lint.OK, out)
        self.assertIn("C901 太複雜 1 處", out)

    def test_ruff_is_asked_for_the_rules_and_files(self):
        self.lint()
        with open(os.environ["FAKE_RUFF_ARGS"]) as f:
            args = json.load(f)
        self.assertEqual(args[args.index("--select") + 1], lint.RULES + ",C901")
        self.assertIn("--isolated", args)
        self.assertEqual(args[args.index("--") + 1:], ["a.py"])

    def test_a_baseline_elsewhere(self):
        other = os.path.join(self.tmp, "plugin-baseline.json")
        self.lint("--baseline", other, "--init")
        self.assertTrue(os.path.exists(other))
        self.assertEqual(self.lint("--baseline", other)[0], lint.OK)


class Against(Base):
    """--against: the baseline may not hold more than the live tree's (or the last release's) did (manager 10-24)."""
    def setUp(self):
        super().setUp()
        write(os.path.join(self.root, "a.py"), "import os\nimport re\n")
        self.say([{"file": "a.py", "code": "F401", "row": 1}])
        self.lint("--init")
        self.old = os.path.join(self.tmp, "old-baseline.json")
        shutil.copy(os.path.join(self.root, "tools", "lint-baseline.json"), self.old)

    def widen(self):
        """The same commit adds a problem and the baseline item that holds it."""
        data = self.baseline()
        data["items"].append({"file": "a.py", "rule": "F401", "line": "import re", "count": 1})
        write(os.path.join(self.root, "tools", "lint-baseline.json"), json.dumps(data))
        self.say([{"file": "a.py", "code": "F401", "row": 1}, {"file": "a.py", "code": "F401", "row": 2}])

    def test_a_widened_baseline_fails(self):
        self.widen()
        self.assertEqual(self.lint()[0], lint.OK, "without --against it would pass")
        code, out = self.lint("--against", self.old)
        self.assertEqual(code, lint.NEW, out)
        self.assertIn("基準檔比原本的多了 1 筆", out)
        self.assertIn("a.py F401 ×1：import re", out)

    def test_a_higher_count_fails_too(self):
        write(os.path.join(self.root, "a.py"), "import os\nimport os\n")
        data = self.baseline()
        data["items"][0]["count"] = 2
        write(os.path.join(self.root, "tools", "lint-baseline.json"), json.dumps(data))
        self.say([{"file": "a.py", "code": "F401", "row": 1}, {"file": "a.py", "code": "F401", "row": 2}])
        code, out = self.lint("--against", self.old)
        self.assertEqual(code, lint.NEW, out)
        self.assertIn("import os", out)

    def test_the_same_or_smaller_passes(self):
        self.assertEqual(self.lint("--against", self.old)[0], lint.OK)
        self.say([])
        self.lint("--shrink")
        self.assertEqual(self.lint("--against", self.old)[0], lint.OK)

    def test_measured_again_for_a_new_ruff_lists_what_was_added_and_passes(self):
        with open(self.old) as f:
            data = json.load(f)
        data["ruff"] = "0.16.10"
        write(self.old, json.dumps(data))
        self.widen()
        code, out = self.lint("--against", self.old)
        self.assertEqual(code, lint.OK, out)
        self.assertIn("基準因升版重量（ruff 0.16.10 → %s），新增 1 筆" % lint.RUFF_VERSION, out)
        self.assertIn("import re", out)

    def test_an_unreadable_old_one_is_a_failure(self):
        write(self.old, "{ half")
        code, out = self.lint("--against", self.old)
        self.assertEqual(code, lint.FAILED)
        self.assertIn("比對用的基準檔讀不出來", out)

    def test_only_with_a_check(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(lint.main(["--root", self.root, "--shrink", "--against", self.old]), lint.FAILED)


class AgainstARevision(Base):
    """--against-rev / --against-upstream: the old baseline from a commit, the upstream before HEAD (#code-review of
    84c8f3e: an in-place deploy also pushes commits made here and not pushed yet)."""
    def git(self, *args, cwd=None):
        subprocess.run(["git", "-C", cwd or self.root, "-c", "user.name=rig", "-c", "user.email=rig@example.com"] +
                       list(args), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=60, check=True)

    def setUp(self):
        super().setUp()
        write(os.path.join(self.root, "a.py"), "import os\n")
        self.say([{"file": "a.py", "code": "F401", "row": 1}])
        self.lint("--init")
        self.git("init", "-q")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "one")

    def widen_and_commit(self):
        write(os.path.join(self.root, "a.py"), "import os\nimport re\n")
        data = self.baseline()
        data["items"].append({"file": "a.py", "rule": "F401", "line": "import re", "count": 1})
        write(os.path.join(self.root, "tools", "lint-baseline.json"), json.dumps(data))
        self.say([{"file": "a.py", "code": "F401", "row": 1}, {"file": "a.py", "code": "F401", "row": 2}])
        self.git("commit", "-q", "-am", "widened")

    def test_a_commit_not_pushed_yet_is_compared_with_the_upstream(self):
        bare = os.path.join(self.tmp, "origin.git")
        subprocess.run(["git", "init", "-q", "--bare", bare], timeout=60, check=True)
        self.git("push", "-q", bare, "HEAD:refs/heads/main")
        self.git("remote", "add", "origin", bare)
        self.git("fetch", "-q", "origin")
        self.git("branch", "-q", "--set-upstream-to=origin/main")
        self.widen_and_commit()
        code, out = self.lint("--against-rev", "HEAD")
        self.assertEqual(code, lint.OK, "HEAD already holds the widened one")
        code, out = self.lint("--against-upstream")
        self.assertEqual(code, lint.NEW, out)
        self.assertIn("基準檔比原本的多了 1 筆", out)

    def test_without_an_upstream_head(self):
        self.widen_and_commit()
        self.assertEqual(self.lint("--against-upstream")[0], lint.OK)  # HEAD: the commit itself
        write(os.path.join(self.root, "a.py"), "import os\nimport re\nimport sys\n")
        data = self.baseline()
        data["items"].append({"file": "a.py", "rule": "F401", "line": "import sys", "count": 1})
        write(os.path.join(self.root, "tools", "lint-baseline.json"), json.dumps(data))  # widened, not committed
        self.say([{"file": "a.py", "code": "F401", "row": n} for n in (1, 2, 3)])
        self.assertEqual(self.lint("--against-upstream")[0], lint.NEW)

    def test_a_revision_without_a_baseline_is_not_compared(self):
        self.assertEqual(self.lint("--against-rev", "HEAD~0")[0], lint.OK)
        os.remove(os.path.join(self.root, "tools", "lint-baseline.json"))
        self.git("commit", "-q", "-am", "no baseline")
        self.lint("--init")
        self.assertEqual(self.lint("--against-rev", "HEAD")[0], lint.OK)

    def test_a_commit_changing_golden_needs_its_line_c148(self):
        """C-148: with --against-rev, each commit since that rev changing a golden file names its cases in a
        "golden:" line (tools/golden check-commits, the tree's own)."""
        shutil.copy(os.path.join(os.path.dirname(HERE), "golden"), os.path.join(self.root, "tools", "golden"))
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "the tool")
        write(os.path.join(self.root, "ops", "tests", "golden", "cli", "x.txt"), "x\n")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "golden without its line")
        code, out = self.lint("--against-rev", "HEAD~1")
        self.assertEqual(code, lint.NEW, out)
        self.assertIn("改了 golden cli/x", out)
        self.git("commit", "-q", "--amend", "-m", "golden with its line\n\ngolden: cli/x — a new case")
        self.assertEqual(self.lint("--against-rev", "HEAD~1")[0], lint.OK)
        self.assertEqual(self.lint()[0], lint.OK, "without --against-rev no commits are read")

    def test_one_kind_of_against_only(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(lint.main(["--root", self.root, "--against-upstream", "--against-rev", "HEAD"]),
                             lint.FAILED)


class NoTimeout(Base):
    def check(self, source, name="b.py"):
        write(os.path.join(self.root, name), source)
        self.say([])
        return self.lint()

    def test_a_call_without_timeout_is_found(self):
        code, out = self.check("import subprocess\nsubprocess.run(['true'])\n")
        self.assertEqual(code, lint.NEW)
        self.assertIn("b.py:2 ALT001", out)

    def test_each_kind_of_call(self):
        code, out = self.check("import subprocess\nsubprocess.check_output(['a'])\nsubprocess.call(['a'])\n"
                               "subprocess.check_call(['a'])\nsubprocess.Popen(['a'])\n")
        self.assertEqual(code, lint.NEW)
        self.assertEqual([r for r in ("b.py:2", "b.py:3", "b.py:4", "b.py:5") if r + " ALT001" in out],
                         ["b.py:2", "b.py:3", "b.py:4"], out)  # Popen: a process kept, not waited on

    def test_timeout_or_kwargs_pass(self):
        code, out = self.check("import subprocess\nsubprocess.run(['a'], timeout=5)\nsubprocess.run(['a'], **kw)\n")
        self.assertEqual(code, lint.OK, out)

    def test_no_timeout_with_a_reason_passes_on_any_line_of_the_call(self):
        code, out = self.check("import subprocess\nsubprocess.run(\n    ['ssh-keygen'],\n"
                               "    stdin=None)  # no-timeout: waits for the person to type\n")
        self.assertEqual(code, lint.OK, out)

    def test_tests_are_not_checked(self):
        code, out = self.check("import subprocess\nsubprocess.run(['a'])\n", name="x/tests/test_b.py")
        self.assertEqual(code, lint.OK, out)

    def test_a_file_that_does_not_parse_is_left_to_ruff(self):
        code, out = self.check("def (:\n")
        self.assertEqual(code, lint.OK, out)

    def test_held_in_the_baseline(self):
        self.check("import subprocess\nsubprocess.run(['true'])\n")
        self.lint("--init")
        self.assertEqual(self.baseline()["items"][0]["rule"], "ALT001")
        self.assertEqual(self.lint()[0], lint.OK)


class Files(Base):
    def test_a_folder_that_is_not_a_repository(self):
        write(os.path.join(self.root, "a.py"), "")
        write(os.path.join(self.root, "bin", "tool"), "#!/usr/bin/env python3\n")
        write(os.path.join(self.root, "bin", "shell"), "#!/bin/sh\n")
        write(os.path.join(self.root, "notes.md"), "")
        write(os.path.join(self.root, ".venv", "lib", "x.py"), "")
        write(os.path.join(self.root, "node_modules", "y.py"), "")
        os.symlink(os.path.join(self.root, "a.py"), os.path.join(self.root, "link.py"))
        self.assertEqual(lint.files(self.root), ["a.py", "bin/tool"])

    def test_a_repository_checks_git_files_and_new_ones_not_ignored(self):
        write(os.path.join(self.root, "a.py"), "")
        write(os.path.join(self.root, "half.py"), "")
        run = lambda *a: subprocess.run(["git", "-C", self.root] + list(a), stdout=subprocess.DEVNULL,
                                        stderr=subprocess.DEVNULL, timeout=60, check=True)
        write(os.path.join(self.root, "ignored.py"), "")
        write(os.path.join(self.root, ".gitignore"), "ignored.py\n")
        run("init", "-q")
        run("add", "a.py")
        self.assertEqual(lint.files(self.root), ["a.py", "half.py"], "a new file not added yet is checked too")

    def test_new_under_counts_new_files_there_only(self):
        """An in-place deploy commits dcbot/: another channel's half-done file elsewhere is not checked."""
        write(os.path.join(self.root, "a.py"), "")
        write(os.path.join(self.root, "dcbot", "bot", "new.py"), "")
        write(os.path.join(self.root, "ops", "wip.py"), "")
        write(os.path.join(self.root, "ops", "kept.py"), "")
        run = lambda *a: subprocess.run(["git", "-C", self.root] + list(a), stdout=subprocess.DEVNULL,
                                        stderr=subprocess.DEVNULL, timeout=60, check=True)
        run("init", "-q")
        run("add", "a.py", "ops/kept.py")
        self.assertEqual(lint.files(self.root, ["dcbot"]), ["a.py", "dcbot/bot/new.py", "ops/kept.py"])
        self.assertEqual(lint.files(self.root, ["dcbot", "ops"]),
                         ["a.py", "dcbot/bot/new.py", "ops/kept.py", "ops/wip.py"])
        self.assertEqual(lint.files(self.root, []), ["a.py", "ops/kept.py"])
        self.say([])
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = lint.main(["--root", self.root, "--new-under", "dcbot"])
        self.assertEqual(code, lint.OK, out.getvalue())

    def test_a_new_file_not_added_yet_fails(self):
        """An in-place deploy checks, then commits: a new file must be checked before it is added."""
        write(os.path.join(self.root, "newmod.py"), "import subprocess\nsubprocess.run(['true'])\n")
        subprocess.run(["git", "-C", self.root, "init", "-q"], stdout=subprocess.DEVNULL, timeout=60, check=True)
        self.say([])
        code, out = self.lint()
        self.assertEqual(code, lint.NEW, out)
        self.assertIn("newmod.py:2 ALT001", out)


class LinksOut(Base):
    """C-207: a committed link to an absolute path fails (2c2 d003d16f took in a worktree's dcbot/.venv link)."""
    def git(self, *a):
        subprocess.run(["git", "-C", self.root] + list(a), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       timeout=60, check=True)

    def test_a_committed_venv_link_to_an_absolute_path_fails(self):
        write(os.path.join(self.root, "a.py"), "")
        os.makedirs(os.path.join(self.root, "dcbot"))
        os.symlink("/srv/engine/dcbot/.venv", os.path.join(self.root, "dcbot", ".venv"))
        write(os.path.join(self.root, ".gitignore"), ".venv/\n")  # (only a folder: the link is not ignored)
        self.git("init", "-q")
        self.git("add", "-A")
        self.say([])
        code, out = self.lint()
        self.assertEqual(code, lint.NEW, out)
        self.assertIn("dcbot/.venv → /srv/engine/dcbot/.venv", out)
        self.assertIn("連結指向絕對路徑", out)

    def test_a_new_link_not_added_yet_fails_an_ignored_one_does_not(self):
        os.symlink("/srv/x", os.path.join(self.root, "new-link"))
        os.symlink("/srv/y", os.path.join(self.root, ".venv"))
        write(os.path.join(self.root, ".gitignore"), ".venv\n")
        self.git("init", "-q")
        self.assertEqual(lint.links_out(self.root), [("new-link", "/srv/x")])
        self.assertEqual(lint.links_out(self.root, ["elsewhere"]), [], "--new-under: new ones there only")

    def test_a_relative_link_passes_and_home_is_absolute_too(self):
        write(os.path.join(self.root, "a.py"), "")
        os.symlink("a.py", os.path.join(self.root, "b.py"))
        os.symlink("~/x", os.path.join(self.root, "c"))
        self.git("init", "-q")
        self.git("add", "-A")
        self.assertEqual(lint.links_out(self.root), [("c", "~/x")])
        os.remove(os.path.join(self.root, "c"))
        self.git("add", "-A")
        self.say([])
        code, out = self.lint()
        self.assertEqual(code, lint.OK, out)

    def test_outside_a_repository_venv_and_the_like_aside(self):
        os.symlink("/usr/lib/python3", os.path.join(self.root, ".venv"))
        os.makedirs(os.path.join(self.root, "sub"))
        os.symlink("/srv/z", os.path.join(self.root, "sub", "out"))
        os.symlink("/srv", os.path.join(self.root, "dirlink"))
        self.assertEqual(lint.links_out(self.root), [("dirlink", "/srv"), ("sub/out", "/srv/z")])


class Ruff(Base):
    def test_no_ruff_says_the_install_line(self):
        os.environ["ALT_RUFF"] = os.path.join(self.tmp, "missing")
        code, out = self.lint()
        self.assertEqual(code, lint.NO_RUFF)
        self.assertIn("    tools/lint --install", out)
        self.assertNotIn(self.root, out)
        self.assertIn(lint.RUFF_VERSION, out)

    def test_another_version_is_no_ruff(self):
        os.environ["FAKE_RUFF_VERSION"] = "0.6.9"
        code, out = self.lint()
        self.assertEqual(code, lint.NO_RUFF)
        self.assertIn("0.6.9", out)
        self.assertIn("tools/lint --install", out)

    def test_the_install_folder_then_path(self):
        os.environ.pop("ALT_RUFF")
        installed = os.path.join(lint.install_dir(), "bin", "ruff")
        write(installed, FAKE.replace("PYTHON", _sys.executable))
        os.chmod(installed, 0o755)
        os.environ["PATH"] = os.path.dirname(self.ruff)
        self.assertEqual(lint.find_ruff()[0], installed)
        os.remove(installed)
        self.assertEqual(lint.find_ruff()[0], self.ruff)

    def test_ruff_failing_is_a_failure(self):
        write(self.ruff, "#!/bin/sh\n[ \"$1\" = --version ] && echo 'ruff %s' && exit 0\necho broken >&2; exit 2\n"
              % lint.RUFF_VERSION)
        write(os.path.join(self.root, "a.py"), "")
        code, out = self.lint()
        self.assertEqual(code, lint.FAILED)
        self.assertIn("broken", out)

    def test_wrong_usage(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(lint.main(["--what"]), lint.FAILED)


if __name__ == "__main__":
    unittest.main()
