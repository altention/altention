"""altention/update_go.py (C-57 U2): altention update check / go / card. The check moves nothing and sorts what this
machine has of its own; go takes only a clean machine (or commits that go on top) the whole way, each step on the
card, and hands anything else to Claude; the 🆕 card comes only from the bot's machine."""
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
import unittest
from contextlib import redirect_stdout
from unittest import mock

from altention import update, update_go
from altention.dc import card as cards
from altention.tests.test_update import Rig, git


class Exec(Exception):
    pass


class GoRig(Rig):
    """test_update's rig (an origin, a place new versions are made, this machine's engine), notify mode, the cards
    recorded instead of posted, this machine the bot's."""

    def setUp(self):
        super().setUp()
        self.inst = os.path.join(self.tmp, "實例 instance")
        os.makedirs(self.inst)
        with open(os.path.join(self.inst, "plugins.json"), "w") as f:
            json.dump({"format": 1, "core": {"update": "notify"}}, f)
        self.cards, self.closed, self.ran, self.execs = [], [], [], []
        self.codes = {"install": (0, "ok core.x\n"), "reconcile": (0, "都齊了\n"), "doctor": (0, "")}
        for p in (mock.patch.dict(os.environ, {"ALT_INSTANCE": self.inst}),
                  mock.patch.object(update_go.cards, "show", self.show),
                  mock.patch.object(update_go.cards, "close", self.close),
                  mock.patch.object(update_go, "bot_machine_here", lambda: self.bot),
                  mock.patch.object(update_go, "bot_elsewhere", lambda: self.elsewhere),
                  mock.patch.object(update_go, "others", lambda: self.machines),
                  mock.patch.object(update_go, "run", self.run_step),
                  mock.patch.object(update_go.os, "execv", self.execv),
                  mock.patch.object(update_go.config, "this_machine", lambda: "lap")):
            p.start()
            self.addCleanup(p.stop)
        self.bot, self.elsewhere, self.machines = True, False, []
        shutil.rmtree(update_go.work_dir(), ignore_errors=True)
        self.addCleanup(shutil.rmtree, update_go.work_dir(), True)

    def show(self, card_id, text, buttons=(), details=None, channel=None):
        cards.build(card_id, text, buttons, details, channel)  # what the real one would refuse, refused here too
        self.cards.append((card_id, text, [b["id"] for b in buttons], buttons, details))
        return True

    def close(self, card_id, text=None, channel=None):
        self.closed.append((card_id, text))
        return True

    def run_step(self, argv, key, env=None):
        self.ran.append(key)
        if key == "update":  # update.py --release, in this process (its subprocesses faked as in test_update)
            out = io.StringIO()
            with mock.patch.dict(os.environ, {k: v for k, v in (env or {}).items() if k == update.ON_A_CARD}), \
                    self.notify, redirect_stdout(out):
                code = update.main(["--release"])
            return code, out.getvalue()
        return self.codes[key]

    def execv(self, path, argv):
        self.execs.append(argv)
        raise Exec()

    def release(self, version, points=("Something new. More about it.",), agents="Agents only."):
        """A commit with a CHANGELOG section for `version` (every one so far kept), tagged and pushed."""
        self.sections = getattr(self, "sections", [])
        self.sections.insert(0, "## v%s — 2026-10-20\n\n### For people\n\n%s\n\n### For agents\n\n- %s\n" % (
            version, "\n".join("- " + p for p in points), agents))
        self.write(self.up, "CHANGELOG.md", "# Changelog\n\n" + "\n".join(self.sections))
        commit = self.publish("tool.py", "X = %r\n" % version)
        git(self.up, "tag", "v" + version)
        git(self.up, "push", "-q", "origin", "v" + version)
        return commit

    def start(self):
        """This machine on v0.1.0, as a fresh install is (with an ops/setup.py: the reconcile step runs)."""
        self.write(self.up, "ops/setup.py", "")
        self.write(self.up, "notes.txt", "the engine's notes\n")  # a file no release here changes
        self.release("0.1.0", ("Start.",))
        git(self.engine, "fetch", "-q", "--tags")
        git(self.engine, "merge", "-q", "--ff-only", "v0.1.0")

    def go(self, *argv):
        """altention update go …, and its second half when it hands over to the new version (exec)."""
        out = io.StringIO()
        with redirect_stdout(out):
            try:
                code = update_go.main(["go"] + list(argv))
            except Exec:
                code = update_go.main(self.execs[-1][2:])
        self.out = out.getvalue()
        return code

    def kind(self):
        with redirect_stdout(io.StringIO()):
            return update_go.check()

    def status(self):
        return git(self.engine, "status", "--porcelain").stdout


class Check(GoRig):
    def test_clean_and_nothing_moved(self):
        self.start()
        self.release("0.2.0")
        head = self.head()
        f = self.kind()
        self.assertEqual((f["kind"], f["tag"], f["now"]), ("clean", "v0.2.0", "v0.1.0"))
        self.assertEqual(self.head(), head, "the check moves nothing")

    def test_nothing_newer(self):
        self.start()
        self.assertEqual(self.kind()["kind"], "none")

    def test_edits_are_asked_every_time_even_when_not_in_the_way(self):
        """Owner 2026-10-19: edits not committed are asked about, whether or not the release touches the file."""
        self.start()
        self.write(self.engine, "mine.txt", "x\n")
        git(self.engine, "add", "mine.txt")
        git(self.engine, "commit", "-qm", "mine")
        self.write(self.engine, "mine.txt", "edited\n")
        self.release("0.2.0")
        f = self.kind()
        self.assertEqual((f["kind"], f["files"]), ("edits", ["mine.txt"]))
        self.assertEqual(self.status(), " M mine.txt\n", "left as it was")

    def test_a_loose_file_with_the_name_of_one_the_release_adds(self):
        self.start()
        self.write(self.engine, "new.txt", "mine\n")
        self.write(self.up, "new.txt", "the engine's\n")
        self.release("0.2.0")
        f = self.kind()
        self.assertEqual((f["kind"], f["files"]), ("untracked", ["new.txt"]))
        with open(os.path.join(self.engine, "new.txt")) as fh:
            self.assertEqual(fh.read(), "mine\n")

    def test_commits_that_go_on_top_and_ones_that_do_not(self):
        self.start()
        self.write(self.engine, "mine.txt", "x\n")
        git(self.engine, "add", "mine.txt")
        git(self.engine, "commit", "-qm", "my own file")
        mine = self.head()
        self.release("0.2.0")
        f = self.kind()
        self.assertEqual(f["kind"], "commits")
        self.assertTrue(any("my own file" in c for c in f["commits"]), f)
        self.assertEqual(self.head(), mine, "tried aside only")
        self.write(self.engine, "tool.py", "X = 'mine'\n")
        git(self.engine, "commit", "-qam", "my tool")
        self.release("0.3.0")
        head = self.head()
        f = self.kind()
        self.assertEqual((f["kind"], f["files"]), ("conflict", ["tool.py"]))
        self.assertEqual((self.head(), self.status()), (head, ""), "a failed trial leaves nothing behind")
        self.assertEqual(len(git(self.engine, "worktree", "list").stdout.splitlines()), 1, "the throwaway worktree gone")

    def test_detached_and_archive(self):
        self.start()
        self.release("0.2.0")
        git(self.engine, "checkout", "-q", "--detach", "v0.1.0")
        self.assertEqual(self.kind()["kind"], "detached")
        bare = os.path.join(self.tmp, "unpacked")
        os.makedirs(bare)
        with mock.patch.object(update, "ENGINE", bare):
            self.assertEqual(self.kind()["kind"], "archive")

    def test_the_lock_update_py_holds_is_handed_to_the_check(self):
        """A real child through bin/altention (as update.py runs `altention update card`): while this process holds the
        engine's git lock, the child is told it holds it (ALT_GIT_LOCK_FD, the fd passed on) and checks at once
        (not told, it waits LOCK_WAIT for a lock that never comes: the round would hang)."""
        import fcntl
        self.start()
        os.makedirs(os.path.join(self.engine, "bin"))
        shutil.copy(os.path.join(os.path.dirname(update.__file__), "..", "bin", "altention"),
                    os.path.join(self.engine, "bin", "altention"))
        self.release("0.2.0")
        with open(os.path.join(self.engine, ".git", update.LOCK_NAME), "a") as held:
            fcntl.flock(held, fcntl.LOCK_EX)
            r = subprocess.run([os.path.join(self.engine, "bin", "altention"), "update", "check", "--json"],
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True, timeout=60,
                               env=dict(os.environ, ALT_GIT_LOCK_FD=str(held.fileno())), pass_fds=(held.fileno(),))
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertEqual(json.loads(r.stdout)["kind"], "clean", r.stdout)

    def test_check_says_it_in_words(self):
        self.start()
        self.release("0.2.0")
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(update_go.main(["check"]), 0)
        self.assertIn("這台沒有改過的東西，可以直接更新。", out.getvalue())


class Go(GoRig):
    def test_clean_goes_the_whole_way_on_the_card(self):
        self.start()
        self.release("0.2.0", ("Two.",))
        self.assertEqual(self.go("v0.2.0", "--card", "update-v0.2.0"), 0, self.out)
        self.assertEqual(self.ran, ["update", "install", "reconcile", "doctor"])
        self.assertIn("--after-move", self.execs[0], "the rest in the new version's own code")
        self.assertTrue(all(c[0] == "update-v0.2.0" for c in self.cards))
        self.assertTrue(self.cards[0][1].startswith("⏳ 先在旁邊試一次"))
        last = self.cards[-1][1]
        self.assertTrue(last.startswith("✅ 引擎已更新到 v0.2.0。"), last)
        self.assertIn("- Two.", last)
        self.assertEqual(self.cards[-1][2], [], "no buttons left")
        self.assertEqual(self.told, [], "no ✅ notice of update.py's own: the card says it")
        with open(os.path.join(self.state, "updated-release")) as f:
            self.assertEqual(json.load(f)["tag"], "v0.2.0", "recorded")
        self.assertFalse(os.path.exists(update_go.progress_path()))
        for c in self.cards:
            for word in ("commit", "rebase", "worktree", "HEAD"):
                self.assertNotIn(word, c[1], "plain words on the card")

    def test_a_newer_release_is_taken_and_what_was_installed_listed(self):
        """Manager 2026-10-20 (3a): v0.2.0 pressed, v0.3.0 out since: v0.3.0 taken, its points and v0.2.0's listed."""
        self.start()
        self.release("0.2.0", ("Two.",))
        self.release("0.3.0", ("Three.",))
        self.assertEqual(self.go("v0.2.0", "--card", "update-v0.2.0"), 0, self.out)
        last = self.cards[-1][1]
        self.assertTrue(last.startswith("✅ 引擎已更新到 v0.3.0（中間的 v0.2.0 也一起裝上了）。"), last)
        self.assertIn("你按的是 v0.2.0；已經有更新的 v0.3.0，直接換到它", last)
        self.assertIn("- Three.", last)
        self.assertIn("- Two.", last)
        self.assertNotIn("Start.", last)

    def test_edits_wait_for_a_person_and_nothing_moves(self):
        self.start()
        self.write(self.engine, "tool.py", "X = 'being edited'\n")
        self.release("0.2.0")
        head = self.head()
        self.assertEqual(self.go("v0.2.0", "--card", "update-v0.2.0"), 1)
        self.assertEqual(self.ran, [], "nothing run")
        self.assertEqual((self.head(), self.status()), (head, " M tool.py\n"))
        card_id, text, ids, buttons, details = self.cards[-1]
        self.assertTrue(text.startswith("📝 還沒更新到 v0.2.0：這台有你自己改過的檔（tool.py）"), text)
        self.assertIn("你的修改都還在", text)
        self.assertEqual(ids, ["ask", "details"])
        self.assertIn("update skill", buttons[0]["arg"])
        self.assertIn("tool.py", buttons[0]["arg"])
        self.assertIn("edits", details)

    def test_update_refused_says_why_in_plain_words(self):
        self.start()
        self.write(self.up, "broken.py", "def x(:\n")
        self.release("0.2.0")
        head = self.head()
        self.assertEqual(self.go("v0.2.0", "--card", "update-v0.2.0"), 1)
        self.assertEqual(self.head(), head)
        self.assertEqual(self.ran, ["update"])
        text, ids = self.cards[-1][1], self.cards[-1][2]
        self.assertTrue(text.startswith("❌ 還沒更新到 v0.2.0：新版本沒通過檢查"), text)
        self.assertNotIn("細節", text)
        self.assertEqual(ids, ["ask", "details"])
        self.assertEqual(self.told, [], "update.py's ⚠️ not pushed: the card says it")
        self.assertIn("update\tcheck\t", self.alerts(), "written down as always")

    def test_a_release_whose_update_go_does_not_load_is_not_taken(self):
        """The rest of go runs in the new version's update_go: one that throws when loaded never arrives."""
        self.start()
        self.write(self.up, "altention/update_go.py", "raise RuntimeError('broken on load')\n")
        self.release("0.2.0")
        head = self.head()
        self.assertEqual(self.go("v0.2.0", "--card", "update-v0.2.0"), 1)
        self.assertEqual((self.head(), self.ran), (head, ["update"]))
        self.assertTrue(self.cards[-1][1].startswith("❌ 還沒更新到 v0.2.0：新版本沒通過檢查"), self.cards[-1][1])

    def test_the_git_lock_held_meanwhile_is_said_plainly(self):
        self.start()
        self.release("0.2.0")
        real = self.run_step

        def locked(argv, key, env=None):
            return (update.LOCKED, "沒有更新：git 鎖被別人拿著\nALT-UPDATE-DONE\n") if key == "update" else real(argv, key, env)
        with mock.patch.object(update_go, "run", locked):
            self.assertEqual(self.go("v0.2.0", "--card", "update-v0.2.0"), 1)
        self.assertTrue(self.cards[-1][1].startswith("❌ 這次沒有更新：引擎現在正在做別的事"), self.cards[-1][1])

    def test_a_step_after_the_move_failing_says_the_engine_moved(self):
        self.start()
        self.release("0.2.0")
        self.codes["install"] = (1, "ok core.a\nfail core.cron 寫不了排程\n")
        self.assertEqual(self.go("v0.2.0", "--card", "update-v0.2.0"), 1)
        self.assertEqual(self.ran, ["update", "install"])
        text = self.cards[-1][1]
        self.assertTrue(text.startswith("❌ 引擎已經換到 v0.2.0，但「安裝新版需要的東西」這一步沒做完：寫不了排程"), text)
        self.assertEqual(self.cards[-1][2], ["ask", "details"])

    def supervisor(self, subject, during):
        """The supervisor going back on this machine while `during` runs (its trial of the new bot failed): a commit
        with `subject` on top of HEAD, as put_back makes it."""
        code = self.codes[during]

        def back():
            git(self.engine, "commit", "-q", "--allow-empty", "-m", subject)
            return code
        self.codes[during] = back

    def run_step(self, argv, key, env=None):
        got = super().run_step(argv, key, env)
        return got() if callable(got) else got

    def test_a_rollback_after_the_move_is_said_not_a_tick(self):
        """#code-review of a5ae915: the bot's trial of the new version failed, the supervisor rolled back while the
        install ran: no ✅, the steps left, said plainly with Claude's help."""
        self.start()
        self.release("0.2.0", ("Two.",))
        self.supervisor("dcbot: roll back to abcdef0 (trial failed)", "install")
        self.assertEqual(self.go("v0.2.0", "--card", "update-v0.2.0"), 1)
        self.assertEqual(self.ran, ["update", "install"], "the rest not run")
        text = self.cards[-1][1]
        self.assertTrue(text.startswith("❌ 新版 v0.2.0 裝上了，但機器人試跑沒過，現在退回舊版在跑"), text)
        self.assertEqual(self.cards[-1][2], ["ask", "details"])
        self.assertFalse(any(c[1].startswith("✅") for c in self.cards))

    def test_a_step_back_found_before_the_tick(self):
        """The supervisor's step back while packages install (C-44), during the last step: looked at before the ✅."""
        self.start()
        self.release("0.2.0")
        self.supervisor("dcbot: step back to abcdef0 while packages install (1234567)", "doctor")
        self.assertEqual(self.go("v0.2.0", "--card", "update-v0.2.0"), 1)
        self.assertEqual(self.ran, ["update", "install", "reconcile", "doctor"])
        self.assertTrue(self.cards[-1][1].startswith("❌ 新版 v0.2.0 裝上了，但機器人試跑沒過"), self.cards[-1][1])
        self.assertFalse(any(c[1].startswith("✅") for c in self.cards))

    def test_questions_and_doctor_are_said_not_failed(self):
        self.start()
        self.release("0.2.0")
        self.codes["reconcile"] = (0, "補 開 #update\n問 要不要開秘書\n問 用量方案\n")
        self.codes["doctor"] = (1, "fail x\n")
        self.codes["install"] = (2, "todo core.y 要你動手\n")
        self.assertEqual(self.go("v0.2.0", "--card", "update-v0.2.0"), 0)
        last = self.cards[-1][1]
        self.assertTrue(last.startswith("✅"), last)
        self.assertIn("有 2 件事要你決定，請到 #setup 回答。", last)
        self.assertIn("系統檢查有沒過的項目", last)
        self.assertIn("安裝還有幾件事要你動手", last)

    def test_reconcile_skipped_when_the_version_has_no_setup(self):
        """A version without ops/setup.py (before S3): the reconcile step is left out, the rest done."""
        self.start()
        os.remove(os.path.join(self.up, "ops", "setup.py"))
        self.release("0.2.0")
        self.assertEqual(self.go("v0.2.0", "--card", "update-v0.2.0"), 0, self.out)
        self.assertEqual(self.ran, ["update", "install", "doctor"])
        self.assertTrue(self.cards[-1][1].startswith("✅ 引擎已更新到 v0.2.0"))

    def test_without_a_card_only_the_terminal(self):
        self.start()
        self.release("0.2.0")
        self.assertEqual(self.go("v0.2.0"), 0)
        self.assertEqual(self.cards, [])
        self.assertIn("✅ 引擎已更新到 v0.2.0", self.out)

    def test_a_card_only_from_the_bot_machine(self):
        self.bot = False
        self.start()
        self.release("0.2.0")
        head = self.head()
        self.assertEqual(self.go("v0.2.0", "--card", "update-v0.2.0"), 2)
        self.assertIn("只在機器人那台", self.out)
        self.assertEqual((self.cards, self.ran, self.head()), ([], [], head))

    def test_one_go_at_a_time(self):
        self.start()
        self.release("0.2.0")
        os.makedirs(self.state, exist_ok=True)
        import fcntl
        with open(os.path.join(self.state, "update-go.lock"), "a") as held:
            fcntl.flock(held, fcntl.LOCK_EX)
            self.assertEqual(self.go("v0.2.0", "--card", "update-v0.2.0"), 1)
        self.assertEqual(self.ran, [])
        self.assertIn("已經有一次更新正在跑", self.cards[-1][1])

    def test_already_there(self):
        self.start()
        self.assertEqual(self.go("v0.1.0", "--card", "update-v0.1.0"), 0)
        self.assertTrue(self.cards[-1][1].startswith("✅ 這台已經是 v0.1.0 了"))
        self.assertEqual(self.ran, [])

    def test_an_interrupted_go_is_taken_up_where_it_stopped(self):
        """Manager 2026-10-20: the update done, then go killed (update-go.json left, done ["update", "install"]): the
        next go does the steps left, from there, on its own card, and does not just say it is there already."""
        self.start()
        self.release("0.2.0", ("Two.",))
        self.codes["reconcile"] = (1, "沒做完：被中斷")
        self.assertEqual(self.go("v0.2.0", "--card", "update-v0.2.0"), 1)
        self.assertEqual(update_go.read_progress()["done"], ["update", "install"])
        self.codes["reconcile"] = (0, "都齊了\n")
        del self.ran[:], self.cards[:]
        self.assertEqual(self.go("v0.2.0", "--card", "update-v0.2.0-again"), 0, self.out)
        self.assertEqual(self.ran, ["reconcile", "doctor"], "install not run again")
        self.assertTrue(self.cards[-1][1].startswith("✅ 引擎已更新到 v0.2.0"), self.cards[-1][1])
        self.assertEqual({c[0] for c in self.cards}, {"update-v0.2.0-again"})
        self.assertIn("上次換到 v0.2.0 之後被中斷了", self.cards[0][1])
        self.assertEqual(self.closed, [("update-v0.2.0", "這次更新被中斷了，接著做的進度在新的那張卡上。")],
                         "the interrupted card does not stay at its ⏳")
        self.assertFalse(os.path.exists(update_go.progress_path()))

    def test_killed_right_after_the_move_before_the_record_said_so(self):
        """#code-review of e3e4267: update.py moved HEAD, the go was killed before writing done ["update"]: the next go
        takes the move as done and does the rest, not "already there"."""
        self.start()
        self.release("0.2.0")
        with self.notify, redirect_stdout(io.StringIO()):
            update.main(["--release"])  # the move, as go's first step did it
        update_go.write_progress({"card": "update-v0.2.0", "asked": "v0.2.0", "tag": "v0.2.0", "start": "v0.1.0",
                                  "done": [], "notes": [], "first": "", "newer": ""})  # as go_first wrote it before
        self.assertEqual(self.go("v0.2.0", "--card", "update-v0.2.0"), 0, self.out)
        self.assertEqual(self.ran, ["install", "reconcile", "doctor"])
        self.assertTrue(self.cards[-1][1].startswith("✅ 引擎已更新到 v0.2.0"), self.cards[-1][1])

    def test_taken_up_on_the_same_card_or_from_a_terminal(self):
        self.start()
        self.release("0.2.0")
        self.codes["install"] = (1, "fail core.x 被中斷\n")
        self.go("v0.2.0", "--card", "update-v0.2.0")
        self.codes["install"] = (0, "ok\n")
        self.assertEqual(self.go("v0.2.0", "--card", "update-v0.2.0"), 0, self.out)
        self.assertEqual(self.closed, [], "the same card: nothing to close")
        self.codes["install"] = (1, "fail core.x 被中斷\n")
        self.release("0.3.0")
        self.go("v0.3.0", "--card", "update-v0.3.0")
        self.codes["install"] = (0, "ok\n")
        self.assertEqual(self.go("v0.3.0"), 0, self.out)
        self.assertEqual(self.closed, [("update-v0.3.0", "這次更新被中斷了，之後在終端機接著做完。")])

    def test_a_record_that_does_not_match_is_said_and_removed(self):
        self.start()
        self.release("0.2.0")
        update_go.write_progress({"card": "update-v0.9.0", "tag": "v0.9.0", "done": ["update"], "start": "v0.1.0"})
        self.assertEqual(self.go("v0.2.0", "--card", "update-v0.2.0"), 0, self.out)
        self.assertIn("紀錄是 v0.9.0，這台是 v0.1.0", self.cards[0][1])
        self.assertEqual(self.closed, [("update-v0.9.0", "這次更新沒做完，已經從頭再更新一次，進度在新的那張卡上。")],
                         "the record's card closed too (#code-review of 394f260)")
        self.assertEqual(self.ran, ["update", "install", "reconcile", "doctor"], "from the start")
        self.assertTrue(self.cards[-1][1].startswith("✅ 引擎已更新到 v0.2.0"))

    def test_taken_up_then_on_to_a_newer_release(self):
        self.start()
        self.release("0.2.0")
        self.codes["install"] = (1, "fail core.x 被中斷\n")
        self.go("v0.2.0")
        self.codes["install"] = (0, "ok\n")
        self.release("0.3.0")
        del self.ran[:]
        self.assertEqual(self.go("v0.3.0"), 0, self.out)
        self.assertEqual(self.ran, ["install", "reconcile", "doctor", "update", "install", "reconcile", "doctor"])
        self.assertIn("✅ 引擎已更新到 v0.3.0", self.out)

    def test_long_text_for_claude_goes_in_a_file_in_setups_folder(self):
        arg = update_go.handed("update-v0.2.0", "x" * (update_go.ASK_INLINE + 1))
        path = arg.split("請讀 ", 1)[1].split("，", 1)[0]
        self.assertTrue(path.startswith(update_go.work_dir() + os.sep), path)
        with open(path) as f:
            self.assertEqual(len(f.read()), update_go.ASK_INLINE + 1)
        self.assertEqual(update_go.handed("update-v0.2.0", "short"), "short")


def hide_identity(case):
    """No git identity anywhere for this test: other tests may leave a ~/.gitconfig in the shared test home, and
    GIT_AUTHOR_*/GIT_COMMITTER_* may be set."""
    env = {k: v for k, v in os.environ.items() if not k.startswith(("GIT_AUTHOR_", "GIT_COMMITTER_"))}
    env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")
    p = mock.patch.dict(os.environ, env, clear=True)
    p.start()
    case.addCleanup(p.stop)


GoRig.no_identity = hide_identity


class SafetyNet(GoRig):
    """C-57 U3: a mark before every move to a release, undo back to it, edits kept by save once asked; nothing lost."""

    def tags(self):
        return git(self.engine, "tag", "-l", "altention/before-*").stdout.split()

    def undo(self):
        out = io.StringIO()
        with redirect_stdout(out):
            code = update_go.main(["undo"])
        self.out = out.getvalue()
        return code

    def mode(self, value):
        with open(os.path.join(self.inst, "plugins.json"), "w") as f:
            json.dump({"format": 1, "core": {"update": value}}, f)

    def test_a_mark_before_the_move_and_undo_back_to_it(self):
        self.start()
        old = self.head()
        self.release("0.2.0")
        self.assertEqual(self.go("v0.2.0", "--card", "update-v0.2.0"), 0, self.out)
        self.assertEqual(self.tags(), ["altention/before-v0.2.0"])
        self.assertEqual(git(self.engine, "rev-parse", "altention/before-v0.2.0").stdout.strip(), old)
        self.assertIn("引擎回到更新前", self.cards[-1][1], "the ✅ says how to go back")
        self.write(self.engine, "LICENSE-mine", "x\n")  # not tracked: never touched
        self.write(self.engine, "tool.py", git(self.engine, "show", "HEAD:tool.py").stdout)  # rewritten as it was:
        os.utime(os.path.join(self.engine, "tool.py"), (2000000000, 2000000000))  # its stat differs, its text not
        del self.ran[:]
        self.assertEqual(self.undo(), 0, self.out)
        self.assertEqual(self.head(), old)
        self.assertIn("已經回到更新前的版本（v0.1.0）", self.out)
        self.assertTrue(os.path.exists(os.path.join(self.engine, "LICENSE-mine")))
        self.assertEqual(self.tags(), [], "the mark used")
        self.assertEqual(self.ran, ["install"])
        self.assertEqual(self.undo(), 1, "nothing further back")
        self.assertIn("沒有更新前的記號", self.out)

    def test_undo_keeps_edits_and_refuses_what_would_lose_them(self):
        self.start()
        self.write(self.up, "two.txt", "two\n")
        self.release("0.2.0")
        self.assertEqual(self.go("v0.2.0"), 0, self.out)
        new = self.head()
        self.write(self.engine, "notes.txt", "mine, after the update\n")
        self.assertEqual(self.undo(), 0, self.out)  # notes.txt the same in both: the edit comes along
        with open(os.path.join(self.engine, "notes.txt")) as f:
            self.assertEqual(f.read(), "mine, after the update\n")
        self.release("0.3.0")
        git(self.engine, "checkout", "-q", "--", "notes.txt")
        self.assertEqual(self.go("v0.3.0"), 0, self.out)
        self.write(self.engine, "two.txt", "mine\n")  # a file the version before does not have
        head = self.head()
        self.assertEqual(self.undo(), 1)
        self.assertIn("你的修改都還在", self.out)
        self.assertEqual(self.head(), head)
        with open(os.path.join(self.engine, "two.txt")) as f:
            self.assertEqual(f.read(), "mine\n")
        self.assertNotEqual(new, head)

    def test_undo_refused_after_more_changes_or_in_release_mode(self):
        self.start()
        self.release("0.2.0")
        self.assertEqual(self.go("v0.2.0"), 0, self.out)
        self.write(self.engine, "mine.txt", "x\n")
        git(self.engine, "add", "mine.txt")
        git(self.engine, "commit", "-qm", "mine after the update")
        head = self.head()
        self.assertEqual(self.undo(), 1)
        self.assertIn("直接回去會丟掉那些變動", self.out)
        self.assertEqual(self.head(), head, "the commit made since kept")
        git(self.engine, "reset", "-q", "--hard", "HEAD~1")
        self.mode("release")
        head = self.head()
        self.assertEqual(self.undo(), 1)
        self.assertIn("引擎改成只通知", self.out)
        self.assertEqual(self.head(), head)

    def test_only_the_newest_marks_kept(self):
        self.start()
        for v in ("0.2.0", "0.3.0", "0.4.0", "0.5.0"):
            self.release(v)
            self.assertEqual(self.go("v" + v), 0, self.out)
        self.assertEqual(sorted(self.tags()), ["altention/before-v0.3.0", "altention/before-v0.4.0", "altention/before-v0.5.0"])
        self.assertEqual(sorted(update.befores()), sorted(self.tags()))
        self.assertEqual(git(self.engine, "ls-remote", "--tags", "origin").stdout.count("altention/"), 0, "never pushed")

    def test_a_move_that_did_not_happen_leaves_no_mark(self):
        self.start()
        self.write(self.engine, "new.txt", "mine\n")
        self.write(self.up, "new.txt", "the engine's\n")
        self.release("0.2.0")
        with self.notify, redirect_stdout(io.StringIO()):
            update.main(["--release"])  # straight, past go's check: the fast-forward refuses
        self.assertEqual(self.tags(), [])
        self.assertEqual(update.befores(), {})

    def test_edits_kept_once_asked_then_the_update_and_back(self):
        """Decision 4: save (Claude asked first), then go takes the kept edits on top; undo goes back with them."""
        self.start()
        self.write(self.engine, "notes.txt", "mine\n")
        self.write(self.up, "other.py", "Y = 2\n")
        self.release("0.2.0")
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(update_go.main(["save", "v0.2.0"]), 0)
        self.assertIn("已經把你改過的檔（notes.txt）保留下來了", out.getvalue())
        self.assertEqual(git(self.engine, "log", "-1", "--format=%s").stdout.strip(), "local edits before v0.2.0")
        self.assertEqual(self.status(), "")
        kept = self.head()
        self.assertEqual(self.kind()["kind"], "commits")
        self.assertEqual(self.go("v0.2.0"), 0, self.out)
        with open(os.path.join(self.engine, "notes.txt")) as f:
            self.assertEqual(f.read(), "mine\n", "the edit there after the update")
        self.assertTrue(os.path.exists(os.path.join(self.engine, "other.py")))
        self.assertEqual(self.undo(), 0, self.out)
        self.assertEqual(self.head(), kept)
        with redirect_stdout(out):
            self.assertEqual(update_go.main(["save", "v0.2.0"]), 0)
        self.assertEqual(self.head(), kept, "nothing to keep: nothing made")

    def test_no_git_identity_asked_kept_then_updated(self):
        """#code-review-2 of 5fb6b99: a machine without a git identity (install.sh only lists it as a todo): save,
        check and go the whole way, the edit on top of the release."""
        self.start()
        for key in ("user.name", "user.email"):
            git(self.engine, "config", "--unset", key)
        git(self.engine, "config", "user.useConfigOnly", "true")
        self.no_identity()  # no guessing from the host's name either
        self.assertNotEqual(subprocess.run(["git", "-C", self.engine, "var", "GIT_AUTHOR_IDENT"], stdout=subprocess.PIPE,
                                           stderr=subprocess.PIPE).returncode, 0, "the rig has no identity")
        self.write(self.engine, "notes.txt", "mine\n")
        self.write(self.up, "other.py", "Y = 2\n")
        self.release("0.2.0")
        with redirect_stdout(io.StringIO()):
            self.assertEqual(update_go.main(["save", "v0.2.0"]), 0)
        self.assertEqual(self.kind()["kind"], "commits")
        self.assertEqual(self.go("v0.2.0"), 0, self.out)
        self.assertEqual(git(self.engine, "log", "-1", "--format=%s").stdout.strip(), "local edits before v0.2.0")
        self.assertEqual(git(self.engine, "merge-base", "--is-ancestor", "v0.2.0", "HEAD", check=False).returncode, 0)
        with open(os.path.join(self.engine, "notes.txt")) as f:
            self.assertEqual(f.read(), "mine\n")

    def test_each_kind_said_plainly_with_what_can_be_done(self):
        def card(prepare):
            self.tearDown()
            self.setUp()
            self.start()
            prepare()
            self.assertEqual(self.go("v0.2.0", "--card", "update-v0.2.0"), 1, self.out)
            text = self.cards[-1][1]
            self.assertTrue(text.startswith("📝 還沒更新到 v0.2.0："), text)
            for word in ("commit", "rebase", "HEAD", "worktree", "stash", "存"):
                self.assertNotIn(word, text)
            return text, self.cards[-1][3][0]["arg"]

        def edits():
            self.write(self.engine, "tool.py", "X = 'mine'\n")
            self.release("0.2.0")

        def untracked():
            self.write(self.engine, "new.txt", "mine\n")
            self.write(self.up, "new.txt", "the engine's\n")
            self.release("0.2.0")

        def conflict():
            self.write(self.engine, "tool.py", "X = 'mine'\n")
            git(self.engine, "commit", "-qam", "my tool")
            self.release("0.2.0")

        def mixed():
            git(self.engine, "commit", "-q", "--allow-empty", "-m", "dcbot: roll back to abcdef0 (trial failed)")
            self.release("0.2.0")

        def detached():
            self.release("0.2.0")
            git(self.engine, "checkout", "-q", "--detach", "HEAD")
        for prepare, words, hint in ((edits, "這台有你自己改過的檔（tool.py）", "altention update save v0.2.0"),
                                     (untracked, "新版本要加的檔案（new.txt），這台已經有同名的檔", "altention update aside v0.2.0"),
                                     (conflict, "你自己改過的地方（tool.py），新版本也改了同一段", "問過再合"),
                                     (mixed, "這台有機器人自己退回的版本", "哪些要留"),
                                     (detached, "這台的引擎停在一個固定的版本上", "回到平常的分支")):
            with self.subTest(prepare.__name__):
                head = self.head() if os.path.isdir(self.engine) else None
                text, arg = card(prepare)
                self.assertIn(words, text)
                self.assertIn("請 Claude 幫忙", text)
                self.assertIn(hint, arg, "what Claude may do, by kind")
                self.assertEqual(self.ran, [], "nothing moved")
                del head


class Apply(GoRig):
    """C-57 U4: where this machine's commits meet the release, go makes a copy under #setup's folder stopped there;
    Claude merges in it, and apply takes it once all of it is merged, checked like any update."""

    def mine(self, rel, text, msg):
        self.write(self.engine, rel, text)
        git(self.engine, "add", rel)
        git(self.engine, "commit", "-qm", msg)

    def apply(self, version="v0.2.0"):
        out = io.StringIO()
        with redirect_stdout(out):
            code = update_go.main(["apply", version])
        self.out = out.getvalue()
        return code

    def conflicted(self):
        self.start()
        self.mine("tool.py", "X = 'mine'\n", "my tool")
        self.release("0.2.0", ("Two.",))
        head = self.head()
        self.assertEqual(self.go("v0.2.0", "--card", "update-v0.2.0"), 1, self.out)
        copy = update_go.copy_path("v0.2.0")
        return head, copy

    def test_a_copy_to_merge_then_apply_takes_it_the_whole_way(self):
        head, copy = self.conflicted()
        self.assertTrue(copy.startswith(update_go.work_dir() + os.sep), "in #setup's folder (M0)")
        with open(os.path.join(copy, "tool.py")) as f:
            self.assertIn("<<<<<<<", f.read())
        self.assertIn(copy, self.cards[-1][3][0]["arg"], "Claude is told where")
        self.assertIn("altention update apply v0.2.0", self.cards[-1][3][0]["arg"])
        self.assertEqual((self.head(), self.status()), (head, ""), "the engine itself untouched")
        self.assertEqual(self.apply(), 1, "markers left: nothing taken")
        self.assertIn("tool.py", self.out)
        self.assertEqual(self.head(), head)
        self.write(copy, "tool.py", "X = 'merged'\n")
        del self.ran[:]
        self.assertEqual(self.apply(), 0, self.out)
        with open(os.path.join(self.engine, "tool.py")) as f:
            self.assertEqual(f.read(), "X = 'merged'\n")
        self.assertEqual(git(self.engine, "merge-base", "--is-ancestor", "v0.2.0", "HEAD", check=False).returncode, 0)
        self.assertEqual(git(self.engine, "log", "-1", "--format=%s").stdout.strip(), "my tool", "the commit kept on top")
        self.assertEqual(git(self.engine, "rev-parse", "altention/before-v0.2.0").stdout.strip(), head, "marked")
        self.assertFalse(os.path.exists(copy), "the copy gone")
        self.assertEqual(len(git(self.engine, "worktree", "list").stdout.splitlines()), 1)
        self.assertEqual(self.ran, ["install", "reconcile", "doctor"])
        self.assertTrue(self.cards[-1][1].startswith("✅ 引擎已更新到 v0.2.0"), self.cards[-1][1])
        self.assertEqual(self.cards[-1][0], "update-v0.2.0")
        self.assertIn("你的修改和新版合好了", self.cards[-2][1])

    def test_two_commits_meeting_it_are_merged_one_after_the_other(self):
        self.start()
        self.mine("tool.py", "X = 'mine 1'\n", "mine 1")
        self.mine("tool.py", "X = 'mine 2'\n", "mine 2")
        self.release("0.2.0")
        self.go("v0.2.0")
        copy = update_go.copy_path("v0.2.0")
        self.write(copy, "tool.py", "X = 'merged 1'\n")
        self.assertEqual(self.apply(), 1)
        self.assertIn("下一段還有要合的檔：tool.py", self.out)
        self.write(copy, "tool.py", "X = 'merged 2'\n")
        self.assertEqual(self.apply(), 0, self.out)
        with open(os.path.join(self.engine, "tool.py")) as f:
            self.assertEqual(f.read(), "X = 'merged 2'\n")

    def test_merged_to_what_the_release_has_leaves_nothing_of_it(self):
        self.start()
        self.mine("tool.py", "X = 'mine'\n", "mine")
        self.release("0.2.0")
        self.go("v0.2.0")
        copy = update_go.copy_path("v0.2.0")
        self.write(copy, "tool.py", "X = '0.2.0'\n")  # the release's own
        self.assertEqual(self.apply(), 0, self.out)
        self.assertEqual(self.head(), git(self.engine, "rev-parse", "v0.2.0^{commit}").stdout.strip())

    def test_no_git_identity_the_copy_and_apply_still_go(self):
        self.start()
        self.mine("tool.py", "X = 'mine'\n", "my tool")
        for key in ("user.name", "user.email"):
            git(self.engine, "config", "--unset", key)
        git(self.engine, "config", "user.useConfigOnly", "true")
        self.no_identity()
        self.release("0.2.0")
        self.go("v0.2.0")
        copy = update_go.copy_path("v0.2.0")
        with open(os.path.join(copy, "tool.py")) as f:
            self.assertIn("<<<<<<<", f.read(), "the copy stopped where they meet, not on the missing identity")
        self.write(copy, "tool.py", "X = 'merged'\n")
        self.assertEqual(self.apply(), 0, self.out)
        with open(os.path.join(self.engine, "tool.py")) as f:
            self.assertEqual(f.read(), "X = 'merged'\n")

    def test_a_fix_on_a_copy_that_is_the_release_itself_goes_on_top(self):
        """#code-review of 1cb4b97: all of this machine's commits merged down to the release (skipped), then a file
        changed in the copy: the release's own commit is not amended; the change goes on top and is taken."""
        self.start()
        self.mine("tool.py", "X = 'mine'\n", "mine")
        self.release("0.2.0")
        self.go("v0.2.0")
        copy = update_go.copy_path("v0.2.0")
        self.write(copy, "tool.py", "X = '0.2.0'\n")  # merged down to the release's own: skipped, HEAD the release
        self.write(self.engine, "tool.py", "X = 'being edited'\n")  # an edit in the engine: taking it is refused
        self.assertEqual(self.apply(), 1, self.out)
        self.assertIn("換不上", self.out)
        git(self.engine, "checkout", "-q", "--", "tool.py")
        self.write(copy, "notes.txt", "added after\n")  # changed in the copy meanwhile
        self.assertEqual(self.apply(), 0, self.out)
        release = git(self.engine, "rev-parse", "v0.2.0^{commit}").stdout.strip()
        self.assertEqual(git(self.engine, "rev-parse", "HEAD~1").stdout.strip(), release, "on top of the release, itself untouched")
        self.assertEqual(git(self.engine, "log", "-1", "--format=%s").stdout.strip(), "local edits merged into v0.2.0")
        with open(os.path.join(self.engine, "notes.txt")) as f:
            self.assertEqual(f.read(), "added after\n")

    def test_refused_without_a_copy_or_when_the_engine_moved_since(self):
        self.start()
        self.release("0.2.0")
        self.assertEqual(self.apply(), 1)
        self.assertIn("沒有 v0.2.0 的合併副本", self.out)
        head, copy = self.tearDown() or self.setUp() or self.conflicted()
        self.mine("other.txt", "x\n", "another of mine, since")
        moved = self.head()
        self.write(copy, "tool.py", "X = 'merged'\n")
        self.assertEqual(self.apply(), 1)
        self.assertIn("這份副本不能用了", self.out)
        self.assertEqual(self.head(), moved)
        self.assertFalse(os.path.exists(copy))

    def test_a_record_naming_another_folder_deletes_nothing(self):
        """#code-review of ea426b3: update-copy.json edited to name another folder: nothing taken from it, nothing
        deleted, said."""
        head, copy = self.conflicted()
        elsewhere = os.path.join(self.tmp, "someone's folder")
        os.makedirs(elsewhere)
        self.write(elsewhere, "keep.txt", "mine\n")
        with open(update_go.copy_record_path()) as f:
            rec = json.load(f)
        rec["path"] = elsewhere
        with open(update_go.copy_record_path(), "w") as f:
            json.dump(rec, f)
        self.mine("other.txt", "x\n", "since")  # HEAD moved too: the path that removes the copy
        self.assertEqual(self.apply(), 1)
        self.assertIn("合併副本的紀錄指到別的資料夾", self.out)
        self.assertTrue(os.path.exists(os.path.join(elsewhere, "keep.txt")))
        with redirect_stdout(io.StringIO()):
            self.assertFalse(update_go.remove_copy(update_go.sync.Repo(self.engine), elsewhere, "v0.2.0"))
        self.assertTrue(os.path.exists(os.path.join(elsewhere, "keep.txt")), "remove_copy itself refuses it too")

    def test_a_merge_that_does_not_load_is_not_taken(self):
        head, copy = self.conflicted()
        self.write(copy, "tool.py", "def broken(:\n")
        self.assertEqual(self.apply(), 1)
        self.assertIn("沒通過檢查", self.out)
        self.assertEqual(self.head(), head)
        self.assertTrue(os.path.isdir(copy), "kept, to fix")
        self.assertEqual(git(self.engine, "tag", "-l", "altention/before-*").stdout, "")
        self.write(copy, "tool.py", "X = 'fixed'\n")  # fixed in the copy's files only, as Claude does (no git)
        self.assertEqual(self.apply(), 0, "#code-review-2 of ea426b3: the fix counts: " + self.out)
        with open(os.path.join(self.engine, "tool.py")) as f:
            self.assertEqual(f.read(), "X = 'fixed'\n")
        self.assertEqual(git(self.engine, "log", "-1", "--format=%s").stdout.strip(), "my tool", "taken into it")
        self.assertEqual(git(self.engine, "rev-list", "--count", "v0.2.0..HEAD").stdout.strip(), "1")


class Aside(GoRig):
    """C-66: the files not tracked here with the name of one the release adds, renamed <name>.mine (asked first),
    never over anything; only those."""

    def aside(self, version="v0.2.0"):
        out = io.StringIO()
        with redirect_stdout(out):
            code = update_go.main(["aside", version])
        self.out = out.getvalue()
        return code

    def read(self, rel):
        with open(os.path.join(self.engine, rel)) as f:
            return f.read()

    def test_renamed_then_the_update_goes(self):
        self.start()
        self.write(self.engine, "new.txt", "mine\n")
        self.write(self.engine, "sub/two.txt", "mine too\n")
        self.write(self.engine, "other.txt", "not in the way\n")
        self.write(self.up, "new.txt", "the engine's\n")
        self.write(self.up, "sub/two.txt", "the engine's\n")
        self.release("0.2.0")
        self.assertEqual(self.kind()["kind"], "untracked")
        self.assertEqual(self.aside(), 0, self.out)
        self.assertIn("new.txt → new.txt.mine", self.out)
        self.assertEqual((self.read("new.txt.mine"), self.read("sub/two.txt.mine")), ("mine\n", "mine too\n"))
        self.assertFalse(os.path.exists(os.path.join(self.engine, "new.txt")))
        self.assertEqual(self.read("other.txt"), "not in the way\n", "only the ones in the way")
        self.assertEqual(self.kind()["kind"], "clean")
        self.assertEqual(self.go("v0.2.0"), 0, self.out)
        self.assertEqual((self.read("new.txt"), self.read("new.txt.mine")), ("the engine's\n", "mine\n"))

    def test_never_over_anything_nor_a_name_the_release_adds(self):
        self.start()
        self.write(self.engine, "new.txt", "mine\n")
        self.write(self.engine, "new.txt.mine", "an older one of mine\n")
        self.write(self.up, "new.txt", "the engine's\n")
        self.write(self.up, "new.txt.mine.2", "the engine's, oddly\n")
        self.release("0.2.0")
        self.assertEqual(self.aside(), 0, self.out)
        self.assertEqual(self.read("new.txt.mine"), "an older one of mine\n")
        self.assertEqual(self.read("new.txt.mine.3"), "mine\n")
        self.assertFalse(os.path.exists(os.path.join(self.engine, "new.txt.mine.2")))

    def test_an_ignored_file_of_the_same_name_is_in_the_way_too(self):
        """#code-review-2 of 0196526 (manager): a file the person ignores (.git/info/exclude) with the name of one the
        release adds was overwritten by the update without a word; now check stops and aside keeps it."""
        self.start()
        with open(os.path.join(self.engine, ".git", "info", "exclude"), "a") as f:
            f.write("mine-ignored.txt\n")
        self.write(self.engine, "mine-ignored.txt", "mine\n")
        self.write(self.up, "mine-ignored.txt", "the engine's\n")
        self.release("0.2.0")
        f = self.kind()
        self.assertEqual((f["kind"], f["files"]), ("untracked", ["mine-ignored.txt"]))
        self.assertEqual(self.go("v0.2.0"), 1, "not taken over it")
        self.assertEqual(self.read("mine-ignored.txt"), "mine\n")
        self.assertEqual(self.aside(), 0, self.out)
        self.assertEqual(self.go("v0.2.0"), 0, self.out)
        self.assertEqual((self.read("mine-ignored.txt"), self.read("mine-ignored.txt.mine")), ("the engine's\n", "mine\n"))

    def test_nothing_in_the_way_nothing_done(self):
        self.start()
        self.write(self.engine, "other.txt", "x\n")
        self.release("0.2.0")
        self.assertEqual(self.aside(), 0)
        self.assertIn("不用做", self.out)
        self.assertEqual(self.read("other.txt"), "x\n")
        self.assertEqual(self.aside("v9.9.9"), 1, "a version git does not know: nothing")

    def test_claude_is_told_to_ask_then_aside(self):
        self.start()
        self.write(self.engine, "new.txt", "mine\n")
        self.write(self.up, "new.txt", "the engine's\n")
        self.release("0.2.0")
        self.go("v0.2.0", "--card", "update-v0.2.0")
        self.assertIn("altention update aside v0.2.0", self.cards[-1][3][0]["arg"])


class StepTimeout(unittest.TestCase):
    def test_a_step_taking_too_long_is_killed_with_what_it_started(self):
        """#code-review of a5ae915: install.sh's git clone or pip went on after its timeout; the whole group goes."""
        import tempfile
        import time
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        pidfile = os.path.join(tmp, "child")
        with mock.patch.object(update, "ENGINE", tmp), mock.patch.dict(update_go.TIMEOUTS, {"install": 1}):
            t = time.time()
            code, out = update_go.run(["sh", "-c", "sleep 60 & echo $! > %s; wait" % pidfile], "install")
        self.assertEqual(code, 124)
        self.assertLess(time.time() - t, 20)
        with open(pidfile) as f:
            child = int(f.read())
        for _ in range(50):
            try:
                os.kill(child, 0)
            except ProcessLookupError:
                break
            time.sleep(0.1)
        else:
            os.kill(child, 9)
            self.fail("the step's own child outlived it")


class Others(GoRig):
    """U5: on the bot's machine, once it is updated, go takes every other machine on the list the same way (ssh),
    one after another, each a line under the card's ✅; one stopping does not stop the rest."""

    def setUp(self):
        super().setUp()
        self.machines = [("mac", {"ssh": ["mac"]}), ("nv", {"ssh": ["nv"]}), ("pi", {"ssh": ["pi"]})]
        self.said = {"mac": (0, "⏳ 先在旁邊試一次…\n✅ 引擎已更新到 v0.2.0。這版的重點：\n- x\n", "mac"),
                     "nv": (1, "⏳ 先在旁邊試一次…\n📝 還沒更新到 v0.2.0：這台有你自己改過的檔（a.py）。為了…\n", "nv"),
                     "pi": (255, "ssh: connect to host pi: No route to host", None)}
        self.asked = []

        def on_machine(m, cmd):
            self.asked.append((m["ssh"][0], cmd))
            return self.said[m["ssh"][0]]
        p = mock.patch.object(update_go, "on_machine", on_machine)
        p.start()
        self.addCleanup(p.stop)

    def test_the_others_after_this_one_each_a_line(self):
        self.start()
        self.release("0.2.0")
        self.assertEqual(self.go("v0.2.0", "--card", "update-v0.2.0"), 0, self.out)
        self.assertEqual([a for a, _ in self.asked], ["mac", "nv", "pi"])
        self.assertTrue(all(c.endswith("/bin/altention update go v0.2.0") and "--card" not in c for _, c in self.asked),
                        "no card from there: only the bot's machine writes cards")
        last = self.cards[-1][1]
        self.assertTrue(last.startswith("✅ 引擎已更新到 v0.2.0"), last)
        self.assertIn("其他台：\n✅ mac：引擎已更新到 v0.2.0。\n", last)
        self.assertNotIn("這版的重點：\n📝", last)
        self.assertIn("📝 nv：還沒更新到 v0.2.0：這台有你自己改過的檔（a.py）。（到 #setup 跟 Claude 說", last)
        self.assertIn("引擎更新卡住了", last)
        self.assertIn("⚠️ pi：連不上", last)
        self.assertTrue(any("⏳ mac：更新中…" in c[1] for c in self.cards), "each going said on the card")

    def test_this_one_stopping_takes_no_other(self):
        self.start()
        self.write(self.engine, "tool.py", "X = 'mine'\n")
        self.release("0.2.0")
        self.assertEqual(self.go("v0.2.0", "--card", "update-v0.2.0"), 1)
        self.assertEqual(self.asked, [])

    def test_not_the_bot_machine_leads_none(self):
        """go run on another machine (as the bot's machine runs it there, or a person in a terminal) takes no other."""
        self.bot = False
        self.start()
        self.release("0.2.0")
        self.assertEqual(self.go("v0.2.0"), 0, self.out)
        self.assertEqual(self.asked, [])

    def test_an_engine_too_old_for_update_go_updates_the_old_way(self):
        self.machines = [("mac", {"ssh": ["mac"]})]
        self.said["mac"] = (2, "altention (short: alt): one entry point for the whole system. …", "mac")
        calls = []

        def on_machine(m, cmd):
            calls.append(cmd)
            return (2, self.said["mac"][1], "mac") if "update go" in cmd else (0, "ok", "mac")
        with mock.patch.object(update_go, "on_machine", on_machine):
            self.start()
            self.release("0.2.0")
            self.assertEqual(self.go("v0.2.0", "--card", "update-v0.2.0"), 0, self.out)
        self.assertIn("python3 altention/update.py --release && ./install.sh", calls[-1])
        self.assertIn("✅ mac：已更新（那台的引擎較舊", self.cards[-1][1])


class WhoIsTheBots(unittest.TestCase):
    """U5 (manager 2026-10-20): the bot's machine is the list's dcbot entry, this one only by name; "local" says how
    it is reached, not who it is. A list that does not know this machine's name: no cards, no leading."""

    def ask(self, me, listed, known):
        with mock.patch.object(update_go.config, "bot_machine", lambda: listed), \
                mock.patch.object(update_go.config, "known_here", lambda: known), \
                mock.patch.object(update_go.config, "inventory", lambda: {"machines": {"a": {}, "b": {}}}):
            return update_go.bot_machine_here(), update_go.bot_elsewhere()

    def test_by_name_and_unknown_names(self):
        self.assertEqual(self.ask("a", ("a", {}, True), True), (True, False))
        self.assertEqual(self.ask("b", ("a", {}, False), True), (False, True))
        self.assertEqual(self.ask("x", ("a", {}, True), False), (False, False), "not known: neither, the notice in words")


class NewCard(GoRig):
    """update.py's 🆕 in notify mode: a card from the bot's machine (altention update card), words elsewhere."""

    def setUp(self):
        super().setUp()
        self.told_cards = []

    def fake_run(self, argv, *a, **k):
        if argv and str(argv[0]).endswith("altention") and argv[1:3] == ["update", "card"]:
            with mock.patch.dict(os.environ, {"ALT_GIT_LOCK_FD": (k.get("env") or {}).get("ALT_GIT_LOCK_FD", "")}), \
                    redirect_stdout(io.StringIO()):
                code = update_go.main(argv[1:][1:])
            self.told_cards.append(argv[-1])
            return subprocess.CompletedProcess(argv, code, "")
        return super().fake_run(argv, *a, **k)

    def available(self):
        with open(os.path.join(self.state, "update-available")) as f:
            return json.load(f)

    def test_the_bot_machine_posts_a_card_with_the_buttons(self):
        self.start()
        self.round()
        self.release("0.2.0", ("Two.",), "Run install.sh.")
        self.round()
        self.assertEqual(self.told, [], "no 🆕 in words")
        card_id, text, ids, buttons, details = self.cards[-1]
        self.assertEqual(card_id, "update-v0.2.0")
        self.assertTrue(text.startswith("🆕 有新版本 v0.2.0 可以更新（這台現在是 v0.1.0）。"), text)
        self.assertIn("- Two.", text)
        self.assertIn("這台沒有改過的東西，可以直接更新。", text)
        self.assertEqual(ids, ["go", "later", "details"])
        self.assertEqual(buttons[0]["arg"], "v0.2.0")
        self.assertIn("Run install.sh.", details)
        self.assertEqual(self.available()["told"], "v0.2.0")
        self.round()
        self.assertEqual(len(self.cards), 1, "once per release")
        self.release("0.3.0", ("Three.",))
        self.round()
        self.assertEqual(self.cards[-1][0], "update-v0.3.0")
        self.assertEqual([c[0] for c in self.closed], ["update-v0.2.0"], "the older card closed")
        self.assertIn("v0.3.0", self.closed[0][1])

    def test_edits_said_plainly_on_the_card(self):
        self.start()
        self.round()
        self.write(self.engine, "tool.py", "X = 'being edited'\n")
        self.release("0.2.0")
        self.round()
        text = self.cards[-1][1]
        self.assertIn("這台有你自己改過的檔，按「更新」會請 Claude 帶你處理，不會直接換。", text)
        self.assertNotIn("tool.py", text, "the file named under 看要做什麼")
        self.assertIn("tool.py", self.cards[-1][4])

    def test_only_the_bot_machine_tells_a_new_release(self):
        """U5: the list names another machine as the bot's: this one tells nothing (that machine's card does)."""
        self.start()
        self.round()
        self.bot, self.elsewhere = False, True
        self.release("0.2.0")
        self.round()
        self.assertEqual((self.cards, self.told), ([], []))
        self.assertEqual(self.available()["told"], "v0.2.0", "not tried again every round")

    def test_no_bot_known_or_a_card_that_fails_the_notice_in_words(self):
        self.start()
        self.round()
        self.bot = False
        self.release("0.2.0")
        self.round()
        self.assertEqual(self.cards, [])
        self.assertEqual(len(self.told), 1)
        self.assertTrue(self.told[0].startswith("🆕 有新版本 v0.2.0"))
        self.bot = True

        def fails(*a, **k):
            raise cards.CardError("送不出去")
        self.release("0.3.0")
        with mock.patch.object(update_go.cards, "show", fails):
            self.round()
        self.assertEqual(len(self.told), 2, "the card not out: the notice in words")
        self.assertTrue(self.told[1].startswith("🆕 有新版本 v0.3.0"))


if __name__ == "__main__":
    unittest.main()
