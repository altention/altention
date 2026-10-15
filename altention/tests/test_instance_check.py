"""The instance's settings checked before it is moved (manager 2026-10-06): an upstream whose machines.json or
plugins.json cannot be read (not JSON, not an object, a format this engine does not know, a section or an entry
that is not an object) is held back. Meanwhile this machine's own commits stay here, unpushed, and the alerts file
and doctor say how many; a fix pushed on top comes in and the next round pushes what waited. The held upstream has
one reading (sync.instance_held), is never let through by itself, and apply.py (ops/instance.py pull
--take-refused) is the way out."""
import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))))
from altention import testing as _testing  # noqa: E402
_testing.guard_home()  # tests never touch the real home (altention/testing.py)
import contextlib
import io
import json
import os
import unittest
from unittest import mock

from altention import config, sync
from altention.tests.test_sync_broken_settings import GOOD, Rig, git

ENGINE_DIR = _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
_sys.path.insert(0, os.path.join(ENGINE_DIR, "ops"))
import instance as instance_cmd  # noqa: E402


def alerts():
    try:
        with open(os.path.expanduser("~/.local/state/altention/alerts")) as f:
            return [l.rstrip("\n").split("\t") for l in f if l.strip()]
    except FileNotFoundError:
        return []


class Check(Rig):
    """settings_problem on trees pushed from elsewhere."""

    def problem_with(self, f, text):
        for other in sync.SETTINGS:  # the other one good again: one thing wrong at a time
            if other != f:
                with open(os.path.join(self.repos["instance"][1], other), "w") as fh:
                    fh.write(GOOD[other])
        git(self.repos["instance"][1], "commit", "-qam", "good again", check=False)  # nothing to commit: fine
        up = self.push_from_elsewhere(f, text, "edits %s" % f)
        repo = sync.Repo(self.repos["instance"][2])
        repo.git("fetch", "-q")
        return sync.settings_problem(repo, up)

    def test_what_is_held_and_what_is_not(self):
        m = json.loads(GOOD["machines.json"])
        cases = [
            ("machines.json", GOOD["machines.json"][:20], "讀不出來"),
            ("machines.json", "[1, 2]\n", "不是 JSON 物件"),
            ("machines.json", json.dumps(dict(m, format="1")), "格式 \"1\""),
            ("machines.json", json.dumps(dict(m, format=True)), "格式 true"),
            ("machines.json", json.dumps(dict(m, format=0)), "格式 0"),
            ("machines.json", json.dumps(dict(m, machines=["box"])), "machines 不是物件"),
            ("machines.json", json.dumps(dict(m, machines={"box": "local"})), "machines.box 不是物件"),
            ("plugins.json", json.dumps({"format": 1, "core": "zh"}), "core 不是物件"),
            ("plugins.json", json.dumps({"format": 1, "plugins": {"progress": None}}), "plugins.progress 不是物件"),
            ("plugins.json", "\ufeff{}", "讀不出來"),  # a BOM: config's json.load does not take it either
        ]
        for f, text, said in cases:
            with self.subTest(file=f, said=said):
                self.assertIn(said, self.problem_with(f, text))
        fine = [  # content is not judged; notes ("_…") and a file left out are not sections
            ("machines.json", json.dumps(dict(m, _doc="說明", machines={"typo": {"ssh": 3}, "_doc": "x"}))),
            ("machines.json", json.dumps({"machines": {}})),  # no format: 1
            ("machines.json", json.dumps(dict(m, format=7))),  # newer than this engine: read as config reads it
            ("plugins.json", json.dumps({"format": 1, "core": {"timezone": 42}, "plugins": {}})),
        ]
        for f, text in fine:
            with self.subTest(file=f, text=text):
                self.assertEqual(self.problem_with(f, text), "")
        seed = self.repos["instance"][1]
        git(seed, "rm", "-q", "plugins.json")
        git(seed, "commit", "-qm", "no plugins.json")
        git(seed, "push", "-q")
        repo = sync.Repo(self.repos["instance"][2])
        repo.git("fetch", "-q")
        self.assertEqual(sync.settings_problem(repo, git(seed, "rev-parse", "HEAD").stdout.strip()), "")
        self.assertRaises(sync.Unread, sync.settings_problem, repo, "0" * 40)  # not known: not a verdict either
        git(seed, "rm", "-q", "machines.json")
        with open(os.path.join(seed, "plugins.json"), "w") as fh:
            fh.write("{")
        git(seed, "add", "plugins.json")
        git(seed, "commit", "-qm", "no machines.json, plugins.json broken")
        git(seed, "push", "-q")
        repo.git("fetch", "-q")
        self.assertIn("machines.json 被刪了", sync.settings_problem(repo, git(seed, "rev-parse", "HEAD").stdout.strip()),
                      "this machine has one, the upstream deleted it")
        with open(os.path.join(seed, "machines.json"), "w") as fh:
            fh.write(GOOD["machines.json"])
        git(seed, "add", "machines.json")
        git(seed, "commit", "-qm", "machines back")
        git(seed, "rm", "-q", "plugins.json")
        git(seed, "commit", "-qm", "no plugins.json again")
        git(seed, "push", "-q")
        repo.git("fetch", "-q")
        self.assertEqual(sync.settings_problem(repo, git(seed, "rev-parse", "HEAD").stdout.strip()), "",
                         "plugins.json deleted: the defaults, not held")

    def test_a_newer_format_comes_in_as_config_reads_it(self):
        """Manager 2026-10-07 (a): an engine rolled back must work with newer settings (config.py); the check is
        never stricter than the reading."""
        m = json.loads(GOOD["machines.json"])
        self.assertEqual(self.problem_with("machines.json", json.dumps(dict(m, format=config.FORMAT + 1))), "")

    def test_no_machine_list_here_and_none_upstream_is_not_held(self):
        seed, here = self.repos["instance"][1], self.repos["instance"][2]
        git(seed, "rm", "-q", "machines.json")
        git(seed, "commit", "-qm", "no list")
        git(seed, "push", "-q")
        git(here, "pull", "-q")  # this machine has none either now
        up = self.push_from_elsewhere("memory/x.md", "x\n", "something else")
        repo = sync.Repo(here)
        repo.git("fetch", "-q")
        self.assertEqual(sync.settings_problem(repo, up), "")


class Held(Rig):
    def setUp(self):
        super().setUp()
        for path in (sync.held_path(), os.path.expanduser("~/.local/state/altention/alerts")):  # one home for all tests
            if os.path.exists(path):
                os.remove(path)
    def remember(self, name):
        with open(os.path.join(self.repos["instance"][2], "memory", name), "w") as f:
            f.write("this machine remembers\n")

    def pushed(self):
        return git(self.repos["instance"][0], "log", "--format=%s", "main").stdout

    def test_held_kept_here_told_then_the_fix_comes_and_what_waited_goes(self):
        bad = self.push_from_elsewhere("machines.json", "{ broken", "breaks the list")
        self.remember("one.md")
        st = sync.run()
        here = self.repos["instance"][2]
        self.assertEqual(self.instance_file("machines.json"), GOOD["machines.json"], "not moved onto it")
        inst = st["instance"]
        self.assertTrue(inst["ok"], "held is not a failing sync: %s" % inst)
        self.assertIn(bad[:12], inst["held"])
        self.assertIn("1 個 commit 還沒推出去", inst["held"])
        self.assertIn("apply.py", inst["held"])
        self.assertIn("one.md", git(here, "show", "--stat", "HEAD").stdout, "committed here")
        self.assertNotIn("sync: memory/knowledge", self.pushed(), "not pushed")
        rec = sync.instance_held()
        self.assertEqual((rec["hash"], rec["ahead"]), (bad, "1"))
        self.remember("two.md")
        st = sync.run()
        rec2 = sync.instance_held()
        self.assertEqual((rec2["hash"], rec2["ahead"], rec2["since"]), (bad, "2", rec["since"]))
        rows = [r for r in alerts() if r[2] == "instance-held"]
        self.assertEqual(len(rows), 1, "one row, counted")
        self.assertIn("2 個 commit", rows[0][3])
        self.assertIn("共 2 次", rows[0][3])
        fix = self.push_from_elsewhere("machines.json", GOOD["machines.json"].replace("box", "box"), "fixes the list")
        st = sync.run()
        self.assertNotIn("held", st["instance"])
        self.assertEqual(git(here, "merge-base", "--is-ancestor", fix, "HEAD", check=False).returncode, 0)
        self.assertIn("sync: memory/knowledge", self.pushed(), "what waited is pushed by the round that takes the fix")
        self.assertEqual(sync.instance_held(), {}, "record gone")
        self.assertTrue(st["engine"]["ok"], st["engine"])

    def test_never_let_through_by_itself_and_apply_is_the_way_out(self):
        bad = self.push_from_elsewhere("plugins.json", '{"format": 1, "core": []}\n', "core not an object")
        self.remember("one.md")
        sync.run()
        with mock.patch.object(sync, "settings_problem", return_value="") as checked:  # a check that changed since
            st = sync.run()
        self.assertFalse(checked.called, "the held upstream is not checked again")
        self.assertIn(bad[:12], st["instance"]["held"])
        out = io.StringIO()
        from altention import engine_id
        with contextlib.redirect_stdout(out), mock.patch.object(engine_id, "refusal", return_value=""):
            self.assertEqual(instance_cmd.main(["pull"]), 1)
            self.assertIn("--take-refused", out.getvalue())
            self.assertEqual(instance_cmd.main(["pull", "--take-refused"]), 0)
        self.assertIn("ALT-STEP-OK instance-pull", out.getvalue())
        self.assertIn("越過了被擋的 %s（plugins.json 的 core 不是物件）" % bad[:12], out.getvalue())
        self.assertEqual(self.instance_file("plugins.json"), '{"format": 1, "core": []}\n')
        self.assertEqual(sync.instance_held(), {})
        sync.run()
        self.assertIn("sync: memory/knowledge", self.pushed(), "the next round pushes what waited")

    def test_something_else_on_top_is_checked_again(self):
        first = self.push_from_elsewhere("machines.json", "{ broken", "breaks the list")
        sync.run()
        self.assertEqual(sync.instance_held()["hash"], first)
        # the list fixed, plugins.json broken in the same push: held again, under the new hash, with the new reason
        seed = self.repos["instance"][1]
        with open(os.path.join(seed, "machines.json"), "w") as f:
            f.write(GOOD["machines.json"])
        git(seed, "commit", "-qam", "fixes the list")
        second = self.push_from_elsewhere("plugins.json", '{"format": 1, "sync": []}\n', "fixes one, breaks another")
        st = sync.run()
        rec = sync.instance_held()
        self.assertEqual(rec["hash"], second)
        self.assertIn("sync 不是物件", rec["why"])
        self.assertIn("sync 不是物件", st["instance"]["held"])
        self.assertEqual(self.instance_file("machines.json"), GOOD["machines.json"])

    def test_the_upstream_checked_is_the_one_taken(self):
        """One fetch: a commit pushed between the check and the move would come in unchecked."""
        repo = sync.Repo(self.repos["instance"][2])
        self.push_from_elsewhere("memory/x.md", "x\n", "fine")
        fetches = []
        real = sync.Repo.git

        def counting(self_, *args, **kw):
            if args[:1] == ("fetch",):
                fetches.append(args)
            return real(self_, *args, **kw)
        with mock.patch.object(sync.Repo, "git", counting), sync.locked(repo):
            self.assertEqual(sync.update_instance(repo), (0, None, ""))
        self.assertEqual(len(fetches), 1)

    def test_a_tree_not_read_this_time_is_held_this_round_only(self):
        """#code-review 2026-10-07: git failing once must not leave a good upstream held for ever."""
        up = self.push_from_elsewhere("memory/x.md", "x\n", "fine")
        self.remember("one.md")
        with mock.patch.object(sync, "settings_problem", side_effect=sync.Unread("讀不到 abc 的 machines.json（逾時）")):
            st = sync.run()
        self.assertIn("這一輪讀不到", st["instance"]["held"])
        self.assertEqual(sync.instance_held(), {}, "not recorded")
        self.assertEqual([r[2] for r in alerts()], ["instance-unread"])
        self.assertNotIn("sync: memory/knowledge", self.pushed())
        st = sync.run()  # git works again: checked again and taken
        self.assertNotIn("held", st["instance"])
        here = self.repos["instance"][2]
        self.assertEqual(git(here, "merge-base", "--is-ancestor", up, "HEAD", check=False).returncode, 0)
        self.assertIn("sync: memory/knowledge", self.pushed())

    def test_what_is_taken_is_the_commit_checked(self):
        """Manager 2026-10-07: a commit fetched into the instance between the check and the move stays out."""
        checked = self.push_from_elsewhere("memory/x.md", "x\n", "checked")
        here = self.repos["instance"][2]
        real = sync.settings_problem

        came = []

        def meanwhile(repo, rev):
            got = real(repo, rev)
            came.append(1)
            self.push_from_elsewhere("machines.json", "{ broken %d" % len(came), "came in meanwhile")
            git(here, "fetch", "-q")  # someone else fetched in this instance: @{u} moved on
            return got
        repo = sync.Repo(here)
        with mock.patch.object(sync, "settings_problem", meanwhile), sync.locked(repo):
            self.assertEqual(sync.update_instance(repo), (0, None, ""))
        self.assertEqual(git(here, "rev-parse", "HEAD").stdout.strip(), checked)
        self.assertEqual(self.instance_file("machines.json"), GOOD["machines.json"])
        with self.subTest("with commits of its own (rebased onto the checked one)"):
            self.remember("mine.md")
            git(here, "add", "-A")
            git(here, "commit", "-qm", "mine")
            checked = self.push_from_elsewhere("machines.json", GOOD["machines.json"], "checked too (the list good again)")
            with mock.patch.object(sync, "settings_problem", meanwhile), sync.locked(repo):
                self.assertEqual(sync.update_instance(repo), (0, None, ""))
            self.assertEqual(git(here, "rev-parse", "HEAD~1").stdout.strip(), checked)
            self.assertEqual(self.instance_file("machines.json"), GOOD["machines.json"])

    def test_a_record_with_a_time_that_is_not_a_number_is_no_record(self):
        os.makedirs(sync.state_dir(), exist_ok=True)
        with open(sync.held_path(), "w") as f:
            json.dump({"hash": "abc", "why": "x", "since": "yesterday"}, f)
        said = []
        self.assertEqual(sync.instance_held(said.append), {})
        self.assertIn("內容不對", said[0])

    def test_an_unreadable_record_is_no_record_and_said(self):
        os.makedirs(sync.state_dir(), exist_ok=True)
        with open(sync.held_path(), "w") as f:
            f.write("{ half")
        said = []
        self.assertEqual(sync.instance_held(said.append), {})
        self.assertIn("當成沒有被擋的紀錄", said[0])
        self.push_from_elsewhere("machines.json", "{ broken", "breaks the list")
        st = sync.run()
        self.assertIn("held", st["instance"], "the next upstream is checked all the same")

if __name__ == "__main__":
    unittest.main()
