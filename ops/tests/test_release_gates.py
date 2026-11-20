"""ops/release_gates.py: the checks of the release checklist's part A that release.py runs before a release, on
throwaway repositories and trees only."""
import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))))
from altention import testing as _testing  # noqa: E402
_testing.guard_home()  # tests never touch the real home (altention/testing.py)
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import release  # noqa: E402
import release_gates as gates  # noqa: E402
import test_release  # noqa: E402

ENGINE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
LT, GT, EQ = "<" * 7, ">" * 7, "=" * 7  # spelt apart, so this file holds no marker of its own


def git(repo, *args):
    return test_release.git(repo, *args)


def section(version, date="2026-11-11", zh=("- **一**：一點",), extra=""):
    return ("## v%s — %s\n\n### For people\n\n- **One.** a point\n\n### For people (zh-TW)\n\n%s\n\n"
            "### For agents\n\nNone.\n%s\n" % (version, date, "\n".join(zh), extra))


class Repo(unittest.TestCase):
    """A repository of its own, with git() bound to it as release.git is to the engine."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="發佈檢查 gates ")
        self.repo = os.path.join(self.tmp, "repo")
        os.makedirs(self.repo)
        git(self.repo, "init", "-q")
        git(self.repo, "checkout", "-q", "-b", "main")
        self.write("README.md", "hello\n")
        self.commit("first")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write(self, rel, text):
        path = os.path.join(self.repo, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)

    def commit(self, msg):
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "--allow-empty", "-m", msg)
        return git(self.repo, "rev-parse", "HEAD")

    def git(self, *args, **kw):
        kw.pop("check", None)
        return subprocess.run(["git", "-C", self.repo] + list(args), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              universal_newlines=True)

    def merge(self, branch, subject):
        git(self.repo, "merge", "-q", "--no-ff", branch, "-m", subject)
        return git(self.repo, "rev-parse", "HEAD")


class Versions(Repo):
    def test_first_unpublished_skips_public_tags(self):
        self.write("CHANGELOG.md", "# C\n\n## Unreleased\n\n- x\n\n" + section("0.3.0") + section("0.2.0"))
        head = self.commit("c")
        self.assertEqual(gates.first_unpublished(self.git, head, {"v0.2.0": "x"}), "0.3.0")
        self.assertIsNone(gates.first_unpublished(self.git, head, {"v0.2.0": "x", "v0.3.0": "y"}))

    def test_last_tag_is_the_newest_in_history_not_the_version_itself(self):
        git(self.repo, "tag", "v0.1.0")
        git(self.repo, "checkout", "-q", "-b", "side")
        self.commit("side")
        git(self.repo, "tag", "v0.9.0")  # not in main's history
        git(self.repo, "checkout", "-q", "main")
        self.commit("two")
        git(self.repo, "tag", "v0.1.10")
        head = self.commit("three")
        git(self.repo, "tag", "v0.2.0")
        self.assertEqual(gates.last_tag(self.git, head, "0.2.0"), "v0.1.10", "v0.1.10 after v0.1.9; v0.2.0 is this one")


class Checklist(unittest.TestCase):
    def setUp(self):
        self.inst = tempfile.mkdtemp(prefix="實例 inst ")
        self.env = mock.patch.dict(os.environ, {"ALT_INSTANCE": self.inst})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        shutil.rmtree(self.inst, ignore_errors=True)

    def put(self, text):
        path = gates.checklist_path("1.2.3")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)

    def template(self, hash_, ticked=None, proof="link"):
        rows = ["hash: %s" % hash_, "golden_intended: 無", "reviewed_exceptions: abc1234、def5678", "branch: main"]
        for b in gates.B_ITEMS:
            on = ticked is None or b in ticked
            rows.append("- [%s] %s 某一項 ｜證明：%s" % ("x" if on else " ", b, proof))
        return "\n".join(rows) + "\n發佈後：\n- [ ] C1 推送 ｜\n"

    def test_from_the_instance_with_its_lines(self):
        self.assertIsNone(gates.read_checklist("1.2.3"))
        self.put(self.template("a" * 40))
        got = gates.read_checklist("1.2.3")
        self.assertTrue(got["path"].startswith(self.inst))
        self.assertEqual(got["hash"], "a" * 40)
        self.assertEqual(gates.words(got["golden_intended"]), [])
        self.assertEqual(gates.words(got["reviewed_exceptions"]), ["abc1234", "def5678"])
        self.assertEqual(got["branch"], "main")
        self.assertEqual(got["items"]["B3"], (True, "link"))

    def test_all_ticked_with_proof_passes_even_pushing(self):
        self.put(self.template("abcdef1"))
        said = []
        gates.checklist(gates.read_checklist("1.2.3"), "abcdef1" + "0" * 33, True, said)
        self.assertEqual(said, [])

    def test_another_hash_is_refused(self):
        self.put(self.template("1234567"))
        said = []
        gates.checklist(gates.read_checklist("1.2.3"), "abcdef1" + "0" * 33, False, said)
        self.assertIn("不是要發的", said[0])

    def test_hash_not_on_the_first_line(self):
        self.put("\n# note\n" + self.template("abcdef1"))
        said = []
        gates.checklist(gates.read_checklist("1.2.3"), "abcdef1" + "0" * 33, False, said)
        self.assertIn("第一行", said[0])

    def test_b11_and_b12_may_wait_unless_pushing(self):
        self.put(self.template("abcdef1", ticked=gates.B_ITEMS[:10]))
        record, commit = gates.read_checklist("1.2.3"), "abcdef1" + "0" * 33
        said = []
        gates.checklist(record, commit, False, said)
        self.assertEqual(said, [])
        gates.checklist(record, commit, True, said)
        self.assertIn("B11、B12", said[0])

    def test_unticked_empty_proof_or_a_placeholder_is_not_done(self):
        text = self.template("abcdef1").replace("- [x] B2 某一項", "- [ ] B2 某一項")
        text = text.replace("- [x] B5 某一項 ｜證明：link", "- [x] B5 某一項 ｜證明：")
        text = text.replace("- [x] B9 某一項", "- [x] B9 退回方式：<寫在這裡>")
        self.put(text)
        said = []
        gates.checklist(gates.read_checklist("1.2.3"), "abcdef1" + "0" * 33, False, said)
        self.assertEqual(len(said), 1)
        self.assertIn("B2、B5、B9", said[0])

    def test_other_angle_brackets_in_a_proof_are_a_proof(self):
        text = self.template("abcdef1").replace("- [x] B4 某一項 ｜證明：link",
                                                "- [x] B4 某一項 ｜證明：#bot-test 卡片叫了 <@42>，link")
        self.put(text)
        self.assertEqual(gates.read_checklist("1.2.3")["items"]["B4"], (True, "#bot-test 卡片叫了 <@42>，link"))
        said = []
        gates.checklist(gates.read_checklist("1.2.3"), "abcdef1" + "0" * 33, True, said)
        self.assertEqual(said, [])

    def test_the_proof_placeholder_left_in_is_not_done(self):
        text = self.template("abcdef1").replace("- [x] B6 某一項 ｜證明：link", "- [x] B6 某一項 ｜證明：<連結或 hash>")
        self.put(text)
        said = []
        gates.checklist(gates.read_checklist("1.2.3"), "abcdef1" + "0" * 33, False, said)
        self.assertEqual(len(said), 1)
        self.assertIn("B6", said[0])


class Changelog(Repo):
    def check(self, body, version="0.3.0", last=None, push=False, today="2026-11-11"):
        self.write("CHANGELOG.md", body)
        head = self.commit("c")
        said, notes = [], []
        gates.changelog_shape(self.git, head, version, last, push, said, notes, today=today)
        return said, notes

    def test_a_good_section_passes(self):
        said, notes = self.check("# C\n\n## Unreleased\n\n" + section("0.3.0") + section("0.2.0", "2026-10-01"))
        self.assertEqual((said, notes), ([], []))

    def test_each_part_missing_is_named(self):
        said, _n = self.check("# C\n\n## v0.3.0 — 2026-11-11\n\n### For people\n\n- x\n")
        self.assertIn("For people (zh-TW)、For agents", said[0])

    def test_seven_zh_points_and_a_wrapped_one(self):
        said, _n = self.check(section("0.3.0", zh=["- **%d**：點" % i for i in range(7)] + ["  接著寫"]))
        self.assertTrue(any("7 點" in s for s in said), said)
        self.assertTrue(any("換行" in s for s in said), said)

    def test_the_date_another_day_refused_only_when_pushing(self):
        said, notes = self.check(section("0.3.0", date="2026-11-10"))
        self.assertEqual(said, [])
        self.assertIn("2026-11-10", notes[0])
        said, notes = self.check(section("0.3.0", date="2026-11-10"), push=True)
        self.assertIn("今天是 2026-11-11", said[0])

    def test_no_date(self):
        said, _n = self.check(section("0.3.0").replace(" — 2026-11-11", ""))
        self.assertIn("沒有日期", said[0])

    def test_unreleased_below_the_version(self):
        said, _n = self.check(section("0.3.0") + "## Unreleased\n\n- x\n\n" + section("0.2.0"))
        self.assertIn("Unreleased", said[0])

    def test_an_older_section_changed_since_the_last_tag(self):
        self.write("CHANGELOG.md", section("0.2.0") + section("0.1.0"))
        self.commit("released")
        git(self.repo, "tag", "v0.2.0")
        said, _n = self.check(section("0.3.0") + section("0.2.0", extra="\n- **Late.** a point put in the wrong place\n")
                              + section("0.1.0"), last="v0.2.0")
        self.assertEqual(len(said), 1)
        self.assertIn("v0.2.0", said[0])
        said, _n = self.check(section("0.3.0") + section("0.2.0") + section("0.1.0"), last="v0.2.0")
        self.assertEqual(said, [])


class Golden(unittest.TestCase):
    def test_changed_cases_must_be_meant(self):
        said = []
        gates.golden(["cli/markers", "dc/post"], "v0.5.1", {"golden_intended": "cli/markers"}, said)
        self.assertIn("dc/post", said[0])
        self.assertNotIn("cli/markers", said[0])
        said = []
        gates.golden(["cli/markers"], "v0.5.1", {"golden_intended": "cli/markers"}, said)
        gates.golden([], "v0.5.1", None, said)
        self.assertEqual(said, [])
        gates.golden(["cli/markers"], "v0.5.1", None, said)  # no checklist (said apart): still named
        self.assertEqual(len(said), 1)


class VersionLine(Repo):
    def setUp(self):
        super().setUp()
        self.origin = os.path.join(self.tmp, "origin.git")
        git(self.tmp, "init", "-q", "--bare", self.origin)
        git(self.repo, "remote", "add", "origin", self.origin)

    def line(self, commit, version, branch="main"):
        said = []
        gates.version_line(self.git, commit, version, {"path": "x.md", "branch": branch}, "origin", said)
        return said

    def test_on_the_branch_even_when_it_moved_on(self):
        cand = self.commit("candidate")
        self.commit("next version's work")
        git(self.repo, "push", "-q", "origin", "main")
        self.assertEqual(self.line(cand, "0.6.0"), [])

    def test_not_on_the_branch(self):
        git(self.repo, "push", "-q", "origin", "main")
        cand = self.commit("not pushed")
        self.assertIn("不在 origin 的 main 上", self.line(cand, "0.6.0")[0])
        self.assertIn("沒有分支 release/v0.6.x", self.line(cand, "0.6.0", "release/v0.6.x")[0])

    def test_no_branch_line(self):
        said = []
        gates.version_line(self.git, git(self.repo, "rev-parse", "HEAD"), "0.6.0", {"path": "x.md", "branch": None},
                           "origin", said)
        self.assertIn("branch:", said[0])

    def test_a_fix_release_holds_the_lines_previous_tag(self):
        git(self.repo, "tag", "v0.5.0")
        git(self.repo, "checkout", "-q", "-b", "fix")
        self.commit("fix 1")
        git(self.repo, "tag", "v0.5.1")
        git(self.repo, "checkout", "-q", "main")
        cand = self.commit("from main, without v0.5.1")
        git(self.repo, "push", "-q", "origin", "main", "fix")
        self.assertIn("v0.5.1", self.line(cand, "0.5.2")[0])
        git(self.repo, "checkout", "-q", "fix")
        cand = self.commit("on the fix line")
        git(self.repo, "push", "-q", "origin", "fix")
        self.assertEqual(self.line(cand, "0.5.2", "fix"), [])
        self.assertIn("找不到同一條線", self.line(cand, "0.7.1", "fix")[0])


class Reviewed(Repo):
    def test_only_what_a_release_merge_brought_in(self):
        git(self.repo, "tag", "v0.1.0")
        direct = self.commit("straight onto main")  # before the branch: an ancestor of the merge's second parent
        git(self.repo, "checkout", "-q", "-b", "feature")
        one = self.commit("feature one")
        git(self.repo, "checkout", "-q", "main")
        self.merge("feature", "release: merge feature")
        git(self.repo, "checkout", "-q", "-b", "other")
        two = self.commit("other work")
        git(self.repo, "checkout", "-q", "main")
        head = self.merge("other", "Merge branch other")  # not L3's
        said = []
        gates.reviewed(self.git, head, "v0.1.0", None, said)
        self.assertIn("2 個", said[0])
        self.assertIn(direct[:12], said[0])
        self.assertIn(two[:12], said[0])
        self.assertNotIn(one[:12], said[0])
        said = []
        gates.reviewed(self.git, head, "v0.1.0", {"reviewed_exceptions": "%s, %s" % (direct[:7], two[:10])}, said)
        self.assertEqual(said, [])
        manager = "%s（標題 commit：CHANGELOG 只加一行，manager 看過）、%s（另一個）" % (direct, two[:9])
        gates.reviewed(self.git, head, "v0.1.0", {"reviewed_exceptions": manager}, said)
        self.assertEqual(said, [], "a reason written right after a hash, as the manager writes it")
        gates.reviewed(self.git, head, "v0.1.0", {"reviewed_exceptions": "無（沒有）"}, said)
        self.assertEqual(len(said), 1)
        said = []
        gates.reviewed(self.git, head, None, None, said)  # no release before: nothing to compare
        self.assertEqual(said, [])

    def test_a_deploy_commit_counts_when_its_tree_is_the_reviewed_hashs(self):
        """C-206: deploy.sh --commit puts a branch on main as one commit of one parent, "release: merge <branch>
        <hash> (…) (reviewed …)": it counts when its tree is that hash's; another tree, or a hash not here, does not."""
        git(self.repo, "tag", "v0.1.0")
        git(self.repo, "checkout", "-q", "-b", "feature")
        self.write("a.py", "x = 1\n")
        reviewed = self.commit("feature work")
        git(self.repo, "checkout", "-q", "main")
        git(self.repo, "checkout", "-q", reviewed, "--", "a.py")
        same = self.commit("release: merge feature %s (a thing) (reviewed %s)" % (reviewed[:8], reviewed[:8]))
        self.write("b.py", "y = 2\n")
        more = self.commit("release: merge feature %s (and something not reviewed)" % reviewed[:8])
        gone = self.commit("release: merge other 0123abcd (a hash not in this repository)")
        plain = self.commit("release: merge without a hash")
        said = []
        gates.reviewed(self.git, plain, "v0.1.0", None, said)
        self.assertIn("3 個", said[0])
        self.assertNotIn(same[:12], said[0])
        self.assertNotIn(reviewed[:12], said[0], "its own commit is not on main's line")
        for c in (more, gone, plain):
            self.assertIn(c[:12], said[0])

    def test_any_subject_with_what_deploy_candidate_appends(self):
        """As deploy_candidate.prepare() writes it: the sender's own words, then "(reviewed h1, h2; h3 lint only)";
        the tree of any of them counts (#code-review on bf72a636: 1629b14b, a46973b0)."""
        git(self.repo, "tag", "v0.1.0")
        git(self.repo, "checkout", "-q", "-b", "feature")
        self.write("a.py", "x = 1\n")
        first = self.commit("work")
        self.write("a.py", "x = 2\n")
        lint = self.commit("lint")
        git(self.repo, "checkout", "-q", "main")
        git(self.repo, "checkout", "-q", lint, "--", "a.py")
        held = self.commit("Held posts: told there (reviewed %s, %s; %s lint only)" % (first[:8], "0123abcd", lint[:8]))
        self.write("a.py", "x = 3\n")
        other = self.commit("Something else (reviewed %s)" % first[:8])
        said = []
        gates.reviewed(self.git, other, "v0.1.0", None, said)
        self.assertIn("1 個", said[0])
        self.assertIn(other[:12], said[0])
        self.assertNotIn(held[:12], said[0])
        self.assertEqual(gates.named_reviewed("x (reviewed aaaa111, bbbb222; cccc333 lint only) (reviewed dddd444)"),
                         ["aaaa111", "bbbb222", "cccc333", "dddd444"])


class Markers(Repo):
    def test_markers_but_not_a_heading_underline(self):
        self.write("doc.md", "Title\n%s\n\ntext\n" % EQ)
        self.write("ok.py", "x = '%s'  # inside a line\n" % LT)
        head = self.commit("clean")
        said = []
        gates.markers(self.git, head, said)
        self.assertEqual(said, [])
        self.write("bad.py", "%s HEAD\na = 1\n%s\na = 2\n%s branch\n" % (LT, EQ, GT))
        head = self.commit("left in")
        gates.markers(self.git, head, said)
        self.assertIn("3 行", said[0])
        self.assertIn("bad.py:1", said[0])


class Heads(Repo):
    def problem(self, head):
        return gates.front_problem("---\n%s\n---\n\nbody\n" % head)

    def test_what_reads_and_what_does_not(self):
        self.assertEqual(self.problem("name: x\ndescription: Plain words, fine"), "")
        self.assertEqual(self.problem('name: x\ndescription: "Has #hash and: colon"'), "")
        self.assertEqual(self.problem("name: x\ndescription: 'it''s fine'"), "")
        self.assertEqual(self.problem("name: x\ndescription: >\n  folded words\n  more"), "")
        self.assertEqual(self.problem("name: x\ndescription: plain\n  continued line"), "")
        nested = "name: x\ndescription: fine\nmetadata:\n  type: user\n  note: has #hash and: colon\nallowed-tools:\n  - Bash\n  -\n"
        self.assertEqual(self.problem(nested), "", "nested mappings and lists are YAML's own (#code-review)")
        self.assertIn("description", self.problem("name: x\ndescription:\n  inner: value"), "a nested description is none")
        self.assertIn("切掉", self.problem("name: x\ndescription: Use it for #channels"))
        self.assertIn("切掉", self.problem("name: x\ndescription: first\n  then #later"))
        self.assertIn("「: 」", self.problem("name: x\ndescription: when: always"))
        self.assertIn("引號", self.problem('name: x\ndescription: "never closed'))
        self.assertIn("後面還有字", self.problem('name: x\ndescription: "closed" then more'))
        self.assertIn("description", self.problem("name: x"))
        self.assertIn("---", gates.front_problem("name: x\n"))
        self.assertIn("收尾", gates.front_problem("---\nname: x\n"))

    def test_skill_and_agent_files_in_the_tree(self):
        self.write("skills/good/SKILL.md", "---\nname: good\ndescription: fine\n---\n")
        self.write("skills/bad/SKILL.md", "---\nname: bad\ndescription: cut #here\n---\n")
        self.write(".claude/agents/helper.md", "---\nname: helper\n---\n")
        self.write("docs/notes.md", "no head, not a skill\n")
        head = self.commit("skills")
        said = []
        gates.heads(self.git, head, said)
        self.assertEqual(len(said), 2, said)
        self.assertTrue(said[0].startswith(".claude/agents/helper.md"), said)
        self.assertTrue(said[1].startswith("skills/bad/SKILL.md"), said)


MAIN = 'BUILTINS = ("!help", "!status")\n'
SLASH = '''PLAIN = {"status": ("d", "!status", True)}
def setup(tree, guild):
    @tree.command(name="btw", description="d", guild=guild)
    async def btw(i):
        pass
'''
ALT = 'TOP = {"doctor": "ops/doctor.py", "apply": "ops/apply.py"}\nDC = {"post": "altention/dc/post.py"}\n'


class Guides(Repo):
    def tree(self, guide, usage, exempt=None):
        self.write("dcbot/bot/main.py", MAIN)
        self.write("dcbot/bot/slash.py", SLASH)
        self.write("bin/altention", ALT)
        self.write("dcbot/GUIDE.md", guide)
        self.write("dcbot/docs/usage.md", usage)
        if exempt is not None:
            self.write(gates.EXEMPT, json.dumps(exempt))
        head = self.commit("docs")
        said = []
        gates.guides(self.git, head, said)
        return said

    def test_the_commands_from_the_code(self):
        bang, slash, sub = gates.commands(self.git, self.tree_head())
        self.assertEqual(bang, {"!help", "!status"})
        self.assertEqual(slash, {"/status", "/btw"})
        self.assertEqual(sub, {"altention doctor", "altention apply", "altention dc post"})

    def tree_head(self):
        self.write("dcbot/bot/main.py", MAIN)
        self.write("dcbot/bot/slash.py", SLASH)
        self.write("bin/altention", ALT)
        return self.commit("code")

    def test_every_one_found_passes(self):
        doc = "- `/status`, `!status`: x\n- `!help`\n- `/btw`：y\n"
        usage = doc + "`altention doctor`、`altention apply`、`altention dc post <頻道>`\n"
        self.assertEqual(self.tree(doc, usage), [])

    def test_missing_ones_listed_and_the_exempt_list_honoured(self):
        doc = "- `/status`, `!status`: x\n- `!helpful` only\n"
        usage = doc + "`altention doctor`\n"
        said = self.tree(doc, usage)
        self.assertEqual(len(said), 1)
        for word in ("!help（GUIDE.md）", "/btw（GUIDE.md）", "/btw（usage.md）", "altention apply（usage.md）",
                     "altention dc post（usage.md）"):
            self.assertIn(word, said[0])
        said = self.tree(doc, usage, {"guide": {"!help": "w", "/btw": "w"},
                                      "usage": {"!help": "w", "/btw": "w", "altention apply": "w", "altention dc post": "w"}})
        self.assertEqual(said, [])

    def test_code_or_documents_not_there(self):
        head = self.commit("bare")
        said = []
        gates.guides(self.git, head, said)
        self.assertEqual(len(said), 5, said)  # three code places, two documents

    def test_the_real_engine_lists_its_commands(self):
        def from_disk(*args):  # git show REV:path, from this engine's files (an exported tree has no .git)
            path = os.path.join(ENGINE, args[1].split(":", 1)[1])
            if args[0] != "show" or not os.path.isfile(path):
                return mock.Mock(returncode=128, stdout="")
            with open(path, encoding="utf-8") as f:
                return mock.Mock(returncode=0, stdout=f.read())
        bang, slash, sub = gates.commands(from_disk, "HEAD")
        self.assertIn("!help", bang)
        self.assertIn("/btw", slash)
        self.assertIn("/status", slash)  # from PLAIN
        self.assertIn("altention dc post", sub)


class Plugins(unittest.TestCase):
    PJ = {"format": 1, "name": "demo", "title": "Demo", "description": "d"}

    def rows(self, **over):
        d = dict(self.PJ, **over)
        return [("demo", "v1.0.0", json.dumps(d))]

    def test_a_plugin_the_candidate_reads(self):
        said = []
        gates.plugins_load(ENGINE, said, rows=self.rows())
        self.assertEqual(said, [], said)

    def test_one_it_cannot_read_or_use(self):
        said = []
        gates.plugins_load(ENGINE, said, rows=[("demo", "v1.0.0", "{not json"), ("gone", "v0.1.0", None)])
        self.assertEqual(len(said), 2, said)
        self.assertIn("gone", said[0])
        self.assertIn("demo 的 v1.0.0", said[1])
        said = []
        gates.plugins_load(ENGINE, said, rows=self.rows(uses={"no.such.interface": 1}))
        self.assertIn("no.such.interface", said[0])

    def test_no_network_refuses(self):
        said = []
        with mock.patch.object(gates, "official_plugins", side_effect=RuntimeError("timed out")):
            gates.plugins_load(ENGINE, said)
        self.assertIn("timed out", said[0])


class DeployList(unittest.TestCase):
    def test_the_suites_are_deploy_shs(self):
        with open(os.path.join(ENGINE, "dcbot", "deploy.sh"), encoding="utf-8") as f:
            m = re.search(r"^\s*for t in ([a-z ]+); do", f.read(), re.M)
        self.assertEqual(tuple(m.group(1).split()), gates.ENGINE_SUITES)


PASS = "import unittest\nclass T(unittest.TestCase):\n    def test_ok(self):\n        pass\n"
HOME_SEEN = '''import os, unittest
class T(unittest.TestCase):
    def test_home(self):
        with open(os.environ["SEEN"], "w") as f:
            f.write(os.environ["HOME"] + "\\n" + os.environ.get("ALT_INSTANCE", "-") + "\\n" + os.environ["TMUX_TMPDIR"])
'''
FLAKY = '''import os, unittest
class T(unittest.TestCase):
    def test_second_time(self):
        mark = os.path.join(os.environ["TMPDIR"], "ran-once")
        if not os.path.exists(mark):
            open(mark, "w").close()
            self.fail("first time")
'''
FAIL = "import unittest\nclass T(unittest.TestCase):\n    def test_no(self):\n        self.fail('no')\n"
SLOW = "import time, unittest\nclass T(unittest.TestCase):\n    def test_slow(self):\n        time.sleep(30)\n"


class Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="測試 tests ")
        self.rec = os.path.join(self.tmp, "records")
        self.p = mock.patch.object(gates, "record_dir", lambda: self.rec)
        self.p.start()
        self.n = 0

    def tearDown(self):
        self.p.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_on(self, files, tree="t" * 40, limit=60):
        self.n += 1
        base = os.path.join(self.tmp, "base%d" % self.n)
        root = os.path.join(base, "tree")
        for rel, text in files.items():
            path = os.path.join(root, rel)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w") as f:
                f.write(text)
        said, notes = [], []
        with mock.patch("sys.stdout") as out:
            ok = gates.tests(tree, root, base, said, notes, limit=limit)
        printed = "".join(c.args[0] for c in out.write.call_args_list)
        return ok, said, notes, printed

    def test_passes_in_a_throwaway_home_and_is_recorded(self):
        seen = os.path.join(self.tmp, "seen")
        with mock.patch.dict(os.environ, {"SEEN": seen, "ALT_INSTANCE": "/real/instance"}):
            ok, said, notes, printed = self.run_on({"tools/tests/test_a.py": HOME_SEEN, "bin/tests/test_b.py": PASS})
        self.assertTrue(ok, (said, printed))
        with open(seen) as f:
            home, instance, tmux = f.read().split("\n")
        self.assertTrue(home.startswith(os.path.join(self.tmp, "base1")), home)
        self.assertNotEqual(home, os.path.expanduser("~"))
        self.assertEqual(instance, "-", "no ALT_ setting of this machine reaches the tests")
        self.assertTrue(tmux.startswith(os.path.join(self.tmp, "base1")))
        self.assertIn("bin ok", printed)
        self.assertIn("tools ok", printed)
        got = gates.record_of("t" * 40)
        self.assertTrue(got["passed"])
        ok, said, notes, printed = self.run_on({"tools/tests/test_a.py": FAIL})  # same tree hash: not run again
        self.assertTrue(ok)
        self.assertIn("不重跑", printed)

    def test_a_failure_is_run_again_and_both_said(self):
        ok, said, notes, printed = self.run_on({"tools/tests/test_flaky.py": FLAKY}, tree="f" * 40)
        self.assertTrue(ok, said)
        self.assertIn("tools FAIL", printed)
        self.assertIn("重跑 ok", printed)
        self.assertIn("重跑才過", notes[0])
        self.assertEqual(gates.record_of("f" * 40)["reran"], ["tools"])

    def test_failing_twice_refuses_and_keeps_the_output(self):
        ok, said, notes, printed = self.run_on({"tools/tests/test_no.py": FAIL, "bin/tests/test_ok.py": PASS},
                                               tree="e" * 40)
        self.assertFalse(ok)
        self.assertIn("tools", said[0])
        self.assertNotIn("bin", said[0])
        kept = os.path.join(self.rec, "e" * 40 + "-failed")
        self.assertTrue(os.path.exists(os.path.join(kept, "first", "tools")))
        self.assertTrue(os.path.exists(os.path.join(kept, "again", "tools")))
        self.assertIsNone(gates.record_of("e" * 40))

    def test_cut_at_the_limit(self):
        ok, said, notes, printed = self.run_on({"tools/tests/test_slow.py": SLOW}, tree="d" * 40, limit=2)
        self.assertFalse(ok)
        with open(os.path.join(self.rec, "d" * 40 + "-failed", "first", "tools")) as f:
            self.assertIn("停掉了", f.read())

    def test_nothing_to_run(self):
        ok, said, _n, _p = self.run_on({"README.md": "x"}, tree="c" * 40)
        self.assertFalse(ok)
        self.assertIn("找不到要跑的測試", said[0])

    def test_dcbot_needs_the_bots_python(self):
        with mock.patch.object(gates, "venv_python", lambda: os.path.join(self.tmp, "no", "python")):
            ok, said, _n, _p = self.run_on({"dcbot/tests/test_x.py": PASS}, tree="b" * 40)
        self.assertFalse(ok)
        self.assertIn("機器人的 Python", said[0])


class Gated(test_release.Rig):
    """release.py with the checks on: refused for each missing piece, passing when all are there."""
    GATE_OFF = False

    def setUp(self):
        super().setUp()
        self.write("CHANGELOG.md", "# Changes\n\n## Unreleased\n\n" + section("0.1.0"))
        self.write("dcbot/bot/main.py", MAIN)
        self.write("dcbot/bot/slash.py", SLASH)
        self.write("bin/altention", ALT)
        doc = "- `/status`, `!status`, `!help`, `/btw`\n"
        self.write("dcbot/GUIDE.md", doc)
        self.write("dcbot/docs/usage.md", doc + "`altention doctor` `altention apply` `altention dc post`\n")
        self.head = self.commit("ready")
        git(self.private, "push", "-q", "origin", "main")
        self.mocks = [mock.patch.object(gates, "official_plugins", lambda: []),
                      mock.patch.object(gates, "local_today", lambda: "2026-11-11"),
                      mock.patch.object(gates, "tests", mock.Mock(side_effect=self._ran_tests))]
        for m in self.mocks:
            m.start()

    def _ran_tests(self, tree, root, base, said, notes):
        self.seen = (tree, os.path.isfile(os.path.join(root, "CHANGELOG.md")))
        return True

    def tearDown(self):
        for m in self.mocks:
            m.stop()
        super().tearDown()

    def checklist(self, hash_, open_=()):
        path = os.path.join(self.inst, "docs", "refactor", "releases", "v0.1.0-checklist.md")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        rows = ["hash: %s" % hash_, "golden_intended: 無", "reviewed_exceptions: 無", "branch: main"]
        rows += ["- [%s] %s x ｜證明：link" % (" " if b in open_ else "x", b) for b in gates.B_ITEMS]
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(rows) + "\n")

    def test_without_its_checklist_refused(self):
        code, out = self.run_("check")
        self.assertEqual(code, 1, out)
        self.assertIn("檢查的版本：v0.1.0", out)
        self.assertIn("沒有這一版的勾選紀錄", out)
        gates.tests.assert_called_once()  # the checklist does not change the tree: tested meanwhile

    def test_a_tree_refused_is_not_tested(self):
        self.checklist(self.head)
        self.write("x.py", "%s HEAD\n" % LT)
        self.commit("marker")
        code, out = self.run_("check")
        self.assertEqual(code, 1, out)
        self.assertIn("衝突標記", out)
        self.assertIn("A9", out)
        gates.tests.assert_not_called()

    def test_all_there_passes_and_runs_the_tests_on_that_tree(self):
        self.checklist(self.head, open_=("B11", "B12"))
        code, out = self.run_("check")
        self.assertEqual(code, 0, out)
        self.assertIn("可以發佈 v0.1.0", out)
        tree = git(self.private, "rev-parse", "HEAD^{tree}")
        self.assertEqual(self.seen, (tree, True), "run on that tree, exported")
        code, out = self.run_("publish", "0.1.0", "--push")
        self.assertEqual(code, 1, out)
        self.assertIn("B11、B12", out)
        self.checklist(self.head)
        code, out = self.run_("publish", "0.1.0", "--push")
        self.assertEqual(code, 0, out)

    def test_check_with_another_version(self):
        code, out = self.run_("check", "--version", "0.0.9")
        self.assertEqual(code, 1, out)
        self.assertIn("沒有「## v0.0.9」", out)
        self.assertEqual(self.run_("check", "--version", "x")[0], 2)
        self.assertEqual(self.run_("publish", "0.1.0", "--version", "0.1.0")[0], 2)

    def test_no_version_in_the_changelog(self):
        self.write("CHANGELOG.md", "# Changes\n\n## Unreleased\n\n- x\n")
        self.commit("only unreleased")
        code, out = self.run_("check")
        self.assertEqual(code, 1, out)
        self.assertIn("--version", out)

    def test_a_plugin_gets_only_the_tree_checks(self):
        said, notes = [], []
        with mock.patch.object(gates, "read_checklist") as read:
            self.write("x.py", "%s HEAD\n" % LT)
            head = self.commit("marker")
            release.gate(head, git(self.private, "rev-parse", "HEAD^{tree}"), "0.1.0", False, "demo", said, notes)
        read.assert_not_called()
        self.assertEqual(len(said), 1, said)
        self.assertIn("衝突標記", said[0])


if __name__ == "__main__":
    unittest.main()
