"""altention.sync against throwaway repos and a throwaway HOME."""
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
import time
import unittest
from unittest import mock

from altention import config, sync

ENGINE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def sh(cwd, *args):
    return subprocess.run(list(args), cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)


def write(path, text, mode="w"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, mode) as f:
        f.write(text)


def read(path):
    with open(path) as f:
        return f.read()


class Repos(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.environ.update({"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com", "GIT_COMMITTER_NAME": "t",
                           "GIT_COMMITTER_EMAIL": "t@example.com"})
        self.origin = os.path.join(self.tmp, "origin.git")
        sh(self.tmp, "git", "init", "-q", "--bare", self.origin)
        sh(self.origin, "git", "symbolic-ref", "HEAD", "refs/heads/main")
        self.a, self.b = os.path.join(self.tmp, "a"), os.path.join(self.tmp, "b")
        sh(self.tmp, "git", "clone", "-q", self.origin, self.a)
        sh(self.a, "git", "checkout", "-q", "-b", "main")
        write(os.path.join(self.a, "knowledge", "gotchas.md"), "one\n")
        write(os.path.join(self.a, "memory", "MEMORY.md"), "- a\n")
        sh(self.a, "git", "add", "-A")
        sh(self.a, "git", "commit", "-q", "-m", "init")
        sh(self.a, "git", "push", "-q", "-u", "origin", "main")
        sh(self.tmp, "git", "clone", "-q", "-b", "main", self.origin, self.b)

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def upstream(self, text="from b\n"):
        sh(self.b, "git", "pull", "-q", "--rebase")  # the other machine is up to date before it writes
        write(os.path.join(self.b, "knowledge", "gotchas.md"), text, "a")
        sh(self.b, "git", "commit", "-qam", "b")
        sh(self.b, "git", "push", "-q")

    def test_both_edit_the_same_line_no_markers_anywhere(self):
        self.upstream()
        write(os.path.join(self.a, "knowledge", "gotchas.md"), "from a\n", "a")
        r = sync.sync_repo(self.a, sync.SHARED, "sync", lambda t: None)
        self.assertFalse(r["ok"])
        self.assertNotIn("<<<<<<<", read(os.path.join(self.a, "knowledge", "gotchas.md")))
        self.assertIn("from a", read(os.path.join(self.a, "knowledge", "gotchas.md")), "this machine's edit is kept (committed)")
        sh(self.b, "git", "pull", "-q")
        self.assertNotIn("<<<<<<<", read(os.path.join(self.b, "knowledge", "gotchas.md")), "nothing broken was pushed")

    def test_a_shared_folder_removed_as_a_whole_is_removed_everywhere(self):
        write(os.path.join(self.a, "global-notes-edited", "host-a-1234.md"), "copy\n")
        self.assertTrue(sync.sync_repo(self.a, ["global-notes-edited"], "sync", lambda t: None)["ok"])
        shutil.rmtree(os.path.join(self.a, "global-notes-edited"))  # doctor said: delete it once looked at
        r = sync.sync_repo(self.a, ["global-notes-edited"], "sync", lambda t: None)
        self.assertTrue(r["ok"], r)
        sh(self.b, "git", "pull", "-q")
        self.assertFalse(os.path.exists(os.path.join(self.b, "global-notes-edited")), "the removal reached the other machine")

    def test_a_folder_in_use_that_vanishes_is_reported_not_removed_everywhere(self):
        shutil.rmtree(os.path.join(self.a, "memory"))  # an accident on this machine
        r = sync.sync_repo(self.a, sync.SHARED, "sync", lambda t: None, protected=sync.SHARED)
        self.assertFalse(r["ok"])
        self.assertIn("memory", r["error"])
        sh(self.b, "git", "pull", "-q")
        self.assertTrue(os.path.isfile(os.path.join(self.b, "memory", "MEMORY.md")), "the other machine keeps it")

    def test_many_deletions_in_a_folder_in_use_wait_for_someone(self):
        for i in range(12):
            write(os.path.join(self.a, "memory", "記憶 %02d.md" % i), "m\n")
        self.assertTrue(sync.sync_repo(self.a, sync.SHARED, "sync", lambda t: None, protected=sync.SHARED)["ok"])
        os.remove(os.path.join(self.a, "memory", "記憶 00.md"))  # one file: as usual
        self.assertTrue(sync.sync_repo(self.a, sync.SHARED, "sync", lambda t: None, protected=sync.SHARED)["ok"])
        for i in range(1, 12):  # most of the folder at once
            os.remove(os.path.join(self.a, "memory", "記憶 %02d.md" % i))
        write(os.path.join(self.a, "knowledge", "new.md"), "still synced\n")
        with mock.patch.object(sync, "state_dir", lambda: os.path.join(self.tmp, "state")):
            r = sync.sync_repo(self.a, sync.SHARED, "sync", lambda t: None, protected=sync.SHARED)
            self.assertFalse(r["ok"])
            self.assertIn("--allow-delete", r["error"])
            sh(self.b, "git", "pull", "-q")
            self.assertEqual(len([n for n in os.listdir(os.path.join(self.b, "memory")) if n.startswith("記憶")]), 11)
            self.assertTrue(os.path.exists(os.path.join(self.b, "knowledge", "new.md")), "the rest still goes out")
            sync.allow_delete(self.a, "memory")
            self.assertTrue(sync.sync_repo(self.a, sync.SHARED, "sync", lambda t: None, protected=sync.SHARED)["ok"])
            sh(self.b, "git", "pull", "-q")
            self.assertEqual([n for n in os.listdir(os.path.join(self.b, "memory")) if n.startswith("記憶")], [])

    def test_separate_edits_meet(self):
        self.upstream()
        write(os.path.join(self.a, "memory", "note.md"), "new memory\n")
        r = sync.sync_repo(self.a, sync.SHARED, "sync", lambda t: None)
        self.assertTrue(r["ok"], r)
        sh(self.b, "git", "pull", "-q")
        self.assertTrue(os.path.exists(os.path.join(self.b, "memory", "note.md")))
        self.assertIn("from b", read(os.path.join(self.a, "knowledge", "gotchas.md")))

    def test_conflict_markers_are_never_committed(self):
        write(os.path.join(self.a, "knowledge", "gotchas.md"), "<<<<<<< Updated upstream\nx\n=======\ny\n>>>>>>> Stashed changes\n")
        r = sync.sync_repo(self.a, sync.SHARED, "sync", lambda t: None)
        self.assertIn("衝突標記", r["error"])
        sh(self.b, "git", "pull", "-q")
        self.assertNotIn("<<<<<<<", read(os.path.join(self.b, "knowledge", "gotchas.md")))

    def test_a_marked_file_beside_a_normal_edit_is_not_committed(self):
        write(os.path.join(self.a, "memory", "MEMORY.md"), "<<<<<<< Updated upstream\nx\n=======\ny\n>>>>>>> Stashed changes\n")
        write(os.path.join(self.a, "memory", "note.md"), "fine\n")
        r = sync.sync_repo(self.a, sync.SHARED, "sync", lambda t: None)
        self.assertIn("衝突標記", r["error"])
        head = sh(self.a, "git", "show", "HEAD:memory/MEMORY.md").stdout
        self.assertNotIn("<<<<<<<", head, "the marked file stays out of the commit")
        self.assertEqual(sh(self.a, "git", "show", "HEAD:memory/note.md").stdout, "fine\n", "the other file went in")
        sh(self.b, "git", "pull", "-q")
        self.assertNotIn("<<<<<<<", read(os.path.join(self.b, "memory", "MEMORY.md")))

    def test_non_ascii_names_and_renames_are_committed_whole(self):
        write(os.path.join(self.a, "knowledge", "踩過的坑.md"), "中文\n")
        write(os.path.join(self.a, "memory", "a note with spaces.md"), "x\n")
        write(os.path.join(self.a, "memory", "MEMORY.md"), "- b\n", "a")
        sh(self.a, "git", "mv", "knowledge/gotchas.md", "knowledge/gotchas2.md")
        r = sync.sync_repo(self.a, sync.SHARED, "sync", lambda t: None)
        self.assertTrue(r["ok"], r)
        self.assertEqual(sh(self.a, "git", "status", "--porcelain", "--", "knowledge", "memory").stdout, "", "nothing left behind")
        names = sh(self.a, "git", "-c", "core.quotePath=false", "ls-files", "knowledge").stdout.split()
        self.assertIn("knowledge/踩過的坑.md", names)
        self.assertIn("a note with spaces.md", sh(self.a, "git", "ls-files", "memory").stdout)
        self.assertNotIn("knowledge/gotchas.md", names)

    def test_an_empty_folder_does_not_stop_the_other(self):
        shutil.rmtree(os.path.join(self.a, "knowledge"))
        sh(self.a, "git", "commit", "-qam", "no knowledge")
        os.makedirs(os.path.join(self.a, "knowledge"))
        write(os.path.join(self.a, "memory", "new.md"), "x\n")
        r = sync.sync_repo(self.a, sync.SHARED, "sync", lambda t: None)
        self.assertTrue(r["ok"], r)
        self.assertEqual(sh(self.a, "git", "show", "HEAD:memory/new.md").stdout, "x\n")

    def test_unrelated_uncommitted_edits_do_not_stop_the_sync(self):
        write(os.path.join(self.a, "code.py"), "work in progress\n")  # another channel, halfway
        for i in range(3):
            self.upstream("b%d\n" % i)
            write(os.path.join(self.a, "memory", "m%d.md" % i), "memory %d\n" % i)
            r = sync.sync_repo(self.a, sync.SHARED, "sync", lambda t: None)
            self.assertTrue(r["ok"], r)
        sh(self.b, "git", "pull", "-q", "--rebase")
        self.assertEqual(sorted(n for n in os.listdir(os.path.join(self.b, "memory")) if n.startswith("m")), ["m0.md", "m1.md", "m2.md"])
        self.assertIn("b2", read(os.path.join(self.a, "knowledge", "gotchas.md")))
        self.assertEqual(read(os.path.join(self.a, "code.py")), "work in progress\n", "the other channel's edit is untouched")
        gd = os.path.join(self.a, ".git")
        self.assertFalse(os.path.isdir(os.path.join(gd, "rebase-merge")))
        self.assertEqual(os.listdir(os.path.join(gd, sync.ASIDE)), [], "no temporary worktree left")

    def test_a_commit_made_here_during_the_rebase_aside_is_not_lost(self):
        self.upstream()
        write(os.path.join(self.a, "memory", "mine.md"), "x\n")
        sh(self.a, "git", "add", "-A")
        sh(self.a, "git", "commit", "-qm", "local")
        real = sync.Repo.git

        def git(repo, *args, **kw):  # right after the worktree is made, someone commits in the real tree
            r = real(repo, *args, **kw)
            if args[:2] == ("worktree", "add"):
                write(os.path.join(self.a, "docs", "new.md"), "someone's\n")
                sh(self.a, "git", "add", "docs/new.md")
                sh(self.a, "git", "commit", "-qm", "someone")
            return r
        with mock.patch.object(sync.Repo, "git", git):
            code, note = sync.update(sync.Repo(self.a))
        self.assertEqual(code, 1, note)
        self.assertIn("someone", sh(self.a, "git", "log", "--oneline", "-3").stdout)
        self.assertEqual(read(os.path.join(self.a, "docs", "new.md")), "someone's\n")
        self.assertEqual(sync.update(sync.Repo(self.a))[0], 0, "next round goes through")
        self.assertIn("someone", sh(self.a, "git", "log", "--oneline", "-4").stdout)

    def test_edits_made_after_a_killed_rebase_are_saved_before_it_is_undone(self):
        self.upstream("from b\n")
        write(os.path.join(self.a, "knowledge", "gotchas.md"), "from a\n", "a")
        sh(self.a, "git", "commit", "-qam", "a")
        sh(self.a, "git", "fetch", "-q")
        sh(self.a, "git", "rebase", "-m", "origin/main")  # -m: the same backend on old git; stops on the conflict: a rebase halfway in the real tree
        self.assertTrue(os.path.isdir(os.path.join(self.a, ".git", "rebase-merge")))
        write(os.path.join(self.a, ".git", "agent-rebase"), "")  # as if one of our jobs started it and was killed
        write(os.path.join(self.a, "memory", "written later.md"), "keep me\n")
        repo = sync.Repo(self.a)
        with sync.locked(repo):
            self.assertIsNone(sync.recover(repo, lambda t: None))
        self.assertFalse(os.path.isdir(os.path.join(self.a, ".git", "rebase-merge")))
        saved = sync.saved_edits(repo)
        self.assertEqual(len(saved), 1)
        copy = os.path.join(self.a, ".git", sync.SAVED, saved[0], "memory", "written later.md")
        self.assertEqual(read(copy), "keep me\n", "nothing wiped: a copy is kept for a person to merge")

    def test_only_gits_own_stale_locks_are_removed(self):
        """recover() removes the lock files git itself makes and leaves every other *.lock: those are flocks (ours,
        deploy.sh's, an older engine's), and removing one that is held lets a second holder in beside the first."""
        gd = os.path.join(self.a, ".git")
        os.makedirs(os.path.join(gd, "worktrees", "w"), exist_ok=True)
        gone = ["index.lock", "HEAD.lock", "ORIG_HEAD.lock", "REBASE_HEAD.lock", "packed-refs.lock", "config.lock",
                "refs/heads/main.lock", "refs/remotes/origin/main.lock", "worktrees/w/index.lock", "worktrees/w/HEAD.lock",
                "logs/HEAD.lock", "logs/refs/heads/main.lock", "logs/refs/remotes/origin/main.lock", "logs/refs/stash.lock",
                "worktrees/w/logs/HEAD.lock", "MERGE_MSG.lock", "AUTO_MERGE.lock", "MERGE_RR.lock", "gc.pid.lock",
                "next-index-4242.lock", "index.stash.4242.lock", "worktrees/w/config.worktree.lock"]
        kept = ["altention-deploy.lock", "agent-git.lock", "my tool.lock", "worktrees/w/someones.lock", "Mine.lock",
                sync.LOCK_NAME]
        old = time.time() - sync.STALE_INDEX_LOCK - 60
        for rel in gone + kept:
            write(os.path.join(gd, rel), "")
            os.utime(os.path.join(gd, rel), (old, old))
        said = []
        repo = sync.Repo(self.a)
        self.assertIsNone(sync.recover(repo, said.append))
        self.assertEqual([r for r in gone if os.path.exists(os.path.join(gd, r))], [], "git's own: removed")
        self.assertEqual([r for r in kept if not os.path.exists(os.path.join(gd, r))], [], "every other name: left")
        self.assertEqual(len(said), len(gone))
        self.assertFalse(any("deploy" in t or "agent-git" in t for t in said))

    def test_a_fresh_lock_of_a_rebase_is_git_at_work(self):
        """A lock inside rebase-merge/ that is fresh: git is in the middle of that rebase; it is left as it is."""
        self.upstream("from b\n")
        write(os.path.join(self.a, "knowledge", "gotchas.md"), "from a\n", "a")
        sh(self.a, "git", "commit", "-qam", "a")
        sh(self.a, "git", "fetch", "-q")
        sh(self.a, "git", "rebase", "-m", "origin/main")
        write(os.path.join(self.a, ".git", "agent-rebase"), "")
        write(os.path.join(self.a, ".git", "rebase-merge", "git-rebase-todo.lock"), "")
        repo = sync.Repo(self.a)
        with sync.locked(repo):
            self.assertIn("還新", sync.recover(repo, lambda t: None) or "")
        self.assertTrue(os.path.isdir(os.path.join(self.a, ".git", "rebase-merge")))

    def test_a_fresh_flock_is_no_git_at_work(self):
        """A rebase of ours left halfway is undone even while deploy.sh holds its fresh altention-deploy.lock: only
        git's own fresh locks mean git is still busy here."""
        self.upstream("from b\n")
        write(os.path.join(self.a, "knowledge", "gotchas.md"), "from a\n", "a")
        sh(self.a, "git", "commit", "-qam", "a")
        sh(self.a, "git", "fetch", "-q")
        sh(self.a, "git", "rebase", "-m", "origin/main")
        write(os.path.join(self.a, ".git", "agent-rebase"), "")
        write(os.path.join(self.a, ".git", "altention-deploy.lock"), "")  # fresh: a --commit deploy holds it
        repo = sync.Repo(self.a)
        with sync.locked(repo):
            self.assertIsNone(sync.recover(repo, lambda t: None))
        self.assertFalse(os.path.isdir(os.path.join(self.a, ".git", "rebase-merge")))
        self.assertTrue(os.path.exists(os.path.join(self.a, ".git", "altention-deploy.lock")))

    def test_git_own_lock_names(self):
        for rel in ("index.lock", "FETCH_HEAD.lock", "shallow.lock", "refs/tags/v1.lock", "worktrees/x/ORIG_HEAD.lock",
                    "rebase-merge/git-rebase-todo.lock", "rebase-merge/author-script.lock", "logs/refs/stash.lock",
                    "worktrees/x/logs/HEAD.lock", "HEADS.lock"):
            self.assertTrue(sync.git_own_lock(rel), rel)
        for rel in ("altention-deploy.lock", "agent-git.lock", "head.lock", "worktrees/x/y/z.lock", "objects/a.lock",
                    "worktrees/index.lock", "Merge.lock", "next-index-x.lock", "refs/heads/main"):
            self.assertFalse(sync.git_own_lock(rel), rel)

    def test_no_remote(self):
        sh(self.a, "git", "remote", "remove", "origin")
        write(os.path.join(self.a, "memory", "x.md"), "x\n")
        self.assertTrue(sync.sync_repo(self.a, sync.SHARED, "sync", lambda t: None)["ok"])


class Links(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp()
        self.engine = os.path.join(self.home, "agent-home")
        self.instance = os.path.join(self.home, "altention", "instance")
        write(os.path.join(self.engine, "memory", "MEMORY.md"), "- engine copy\n")
        write(os.path.join(self.engine, "memory", "only-here.md"), "x\n")
        write(os.path.join(self.engine, "memory", "中文 記憶.md"), "y\n")
        write(os.path.join(self.instance, "memory", "MEMORY.md"), "- instance copy\n")
        write(os.path.join(self.instance, "ssh_config"), "Host X\n")
        write(os.path.join(self.home, ".ssh", "config"), "Include %s/ops/ssh_config\n\nHost mine\n" % self.engine)
        self.p = [mock.patch.dict(os.environ, {"HOME": self.home}), mock.patch.object(config, "ENGINE", self.engine),
                  mock.patch.object(config, "this_machine", return_value="host-a")]
        for p in self.p:
            p.start()
        for link in sync.memory_links():
            os.makedirs(os.path.dirname(link), exist_ok=True)
            if not os.path.lexists(link):
                os.symlink(os.path.join(self.engine, "memory"), link)

    def tearDown(self):
        for p in self.p:
            p.stop()
        shutil.rmtree(self.home)

    def test_links_move_to_the_instance_and_nothing_is_lost(self):
        out = sync.repair_links(self.instance, lambda t: None)
        for link in sync.memory_links():
            self.assertEqual(os.path.realpath(link), os.path.realpath(os.path.join(self.instance, "memory")))
        mem = os.path.join(self.instance, "memory")
        self.assertTrue(os.path.exists(os.path.join(mem, "only-here.md")))
        self.assertTrue(os.path.exists(os.path.join(mem, "中文 記憶.md")))
        self.assertIn("instance copy", read(os.path.join(mem, "MEMORY.md")))
        self.assertIn("engine copy", read(os.path.join(mem, "MEMORY.host-a.md")), "same name, other content: both kept")
        self.assertIn("MEMORY.host-a.md", out["twice"])
        self.assertIn("Include %s/ssh_config" % self.instance, read(os.path.join(self.home, ".ssh", "config")))
        self.assertIn("Host mine", read(os.path.join(self.home, ".ssh", "config")))
        self.assertEqual(sync.repair_links(self.instance, lambda t: None)["twice"], [], "second run: nothing to do")

    def test_the_ssh_include_goes_first_and_every_other_byte_stays(self):
        cfg = os.path.join(self.home, ".ssh", "config")
        for before in ("Host a\n  User x\n", "Host a\n\nInclude ~/.orbstack/ssh/config\n"):
            write(cfg, before)
            sync.repair_links(self.instance, lambda t: None)
            self.assertEqual(read(cfg), "Include %s/ssh_config\n\n%s" % (self.instance, before))
            sync.repair_links(self.instance, lambda t: None)
            self.assertEqual(read(cfg), "Include %s/ssh_config\n\n%s" % (self.instance, before), "once")
        write(cfg, "Include %s/ssh_config" % self.instance)  # switched by the old rule: no newline at the end
        sync.repair_links(self.instance, lambda t: None)
        self.assertEqual(read(cfg), "Include %s/ssh_config\n" % self.instance, "the newline is put back")

    def test_after_the_engine_copies_are_gone_links_stand_in_their_place(self):
        shutil.rmtree(os.path.join(self.engine, "memory"))
        write(os.path.join(self.instance, "knowledge", "README.md"), "k\n")
        sync.set_owns(self.instance, ["knowledge"], True)
        states, _ = sync.adopt_all(self.instance, lambda t: None)
        for name in ("knowledge", "memory"):
            path = os.path.join(self.engine, name)
            self.assertTrue(os.path.islink(path))
            self.assertEqual(os.path.realpath(path), os.path.realpath(os.path.join(self.instance, name)))
            self.assertEqual(states[name], "link")

    def test_global_notes_name_this_machines_engine_in_full(self):
        """{engine} becomes this engine's path (manager 2026-10-14: Claude is told full paths, not a bare altention)."""
        g = os.path.join(self.home, ".claude", "CLAUDE.md")
        write(os.path.join(self.engine, "CLAUDE.global.md"), "- `{engine}/bin/altention dc post <頻道> \"…\"`\n")
        self.assertEqual(sync.compose_global(self.instance, lambda t: None), "composed")
        engine = os.path.realpath(self.engine)
        home = os.path.realpath(self.home)
        want = "~" + engine[len(home):] if engine.startswith(home + "/") else engine
        want = sync.config.shell_path(want) if re.search(r"[^\w/~.+-]", want) else want
        self.assertIn("- `%s/bin/altention dc post" % want, read(g))
        self.assertNotIn("{engine}", read(g))

    def test_global_notes_are_the_engines_and_the_instances_together(self):
        g = os.path.join(self.home, ".claude", "CLAUDE.md")
        write(os.path.join(self.engine, "CLAUDE.global.md"), "# 通用\n- tools\n")
        os.makedirs(os.path.dirname(g), exist_ok=True)
        os.symlink(os.path.join(self.engine, "CLAUDE.global.md"), g)  # how install.sh left it
        self.assertEqual(sync.compose_global(self.instance, lambda t: None), "composed")
        self.assertFalse(os.path.islink(g), "the link is replaced, never written through")
        self.assertEqual(read(os.path.join(self.engine, "CLAUDE.global.md")), "# 通用\n- tools\n")
        write(os.path.join(self.instance, "CLAUDE.global.md"), "# 這個安裝\n- 繁體中文\n")
        sync.compose_global(self.instance, lambda t: None)
        text = read(g)
        self.assertTrue(text.startswith(sync.GLOBAL_MARK))
        self.assertLess(text.index("- tools"), text.index("- 繁體中文"))
        said = []
        sync.compose_global(self.instance, said.append)
        self.assertEqual(said, [], "nothing to do the second time")
        with open(g, "a") as f:  # "remember this" at the user level: a line added at the end
            f.write("- 新記住的一條\n")
        sync.compose_global(self.instance, lambda t: None)
        self.assertIn("- 新記住的一條", read(os.path.join(self.instance, "CLAUDE.global.md")), "kept in the instance")
        self.assertIn("- 新記住的一條", read(g))
        self.assertEqual(read(g).count("- 新記住的一條"), 1)
        write(g, read(g).replace("- tools", "- tools, changed by hand"))  # a line changed in the middle
        self.assertEqual(sync.compose_global(self.instance, lambda t: None), "edited")
        self.assertIn("changed by hand", read(g), "not overwritten")
        kept = os.listdir(os.path.join(self.instance, sync.EDITED))
        self.assertEqual(len(kept), 1)
        self.assertIn("changed by hand", read(os.path.join(self.instance, sync.EDITED, kept[0])))
        self.assertEqual(sync.compose_global(self.instance, lambda t: None), "edited", "held until someone looks")
        self.assertEqual(len(os.listdir(os.path.join(self.instance, sync.EDITED))), 1, "one copy, not one per run")
        write(g, sync.GLOBAL_MARK + "\nsomething else entirely\n")  # replaced as a whole
        self.assertEqual(sync.compose_global(self.instance, lambda t: None), "edited")
        self.assertEqual(len(os.listdir(os.path.join(self.instance, sync.EDITED))), 2)
        os.remove(g)  # someone looked: the next run composes again
        self.assertEqual(sync.compose_global(self.instance, lambda t: None), "composed")
        os.remove(os.path.join(self.home, ".local", "state", "altention", "global.last"))  # the record lost, nobody edited
        self.assertEqual(sync.compose_global(self.instance, lambda t: None), "composed")
        write(os.path.join(self.instance, "CLAUDE.global.md"), "# 這個安裝\n- 之後才加的\n")
        self.assertEqual(sync.compose_global(self.instance, lambda t: None), "composed")
        self.assertIn("- 之後才加的", read(g), "still updated after the record was lost")
        os.remove(os.path.join(self.home, ".local", "state", "altention", "global.last"))
        write(g, read(g) + "- edited while the record was gone\n")
        n = len(os.listdir(os.path.join(self.instance, sync.EDITED)))
        self.assertEqual(sync.compose_global(self.instance, lambda t: None), "composed", "written as usual")
        self.assertEqual(len(os.listdir(os.path.join(self.instance, sync.EDITED))), n + 1, "after keeping a copy")
        os.remove(g)
        self.assertEqual(sync.compose_global(self.instance, lambda t: None), "composed")
        self.assertIn("- tools\n", read(g))
        write(g, "my own notes\n")  # a file of the user's own
        self.assertEqual(sync.compose_global(self.instance, lambda t: None), "own")
        self.assertEqual(read(g), "my own notes\n")

    def test_a_real_memory_folder_is_kept_not_deleted(self):
        link = sync.memory_links()[0]
        os.remove(link)
        write(os.path.join(link, "local.md"), "local\n")
        sync.repair_links(self.instance, lambda t: None)
        self.assertTrue(os.path.islink(link))
        self.assertTrue(os.path.exists(os.path.join(self.instance, "memory", "local.md")))
        self.assertTrue([n for n in os.listdir(os.path.dirname(link)) if n.startswith("memory.before-instance-")])


class Run(unittest.TestCase):
    def test_run_never_raises_and_writes_its_status(self):
        home = tempfile.mkdtemp()
        with mock.patch.dict(os.environ, {"HOME": home}), \
                mock.patch.object(config, "instance_dir", side_effect=config.ConfigError("broken pointer")), \
                mock.patch.object(config, "ENGINE", home):
            st = sync.run()
        self.assertIn("broken pointer", st["config_error"])
        self.assertTrue(os.path.exists(os.path.join(home, ".local", "state", "altention", "sync.json")))
        shutil.rmtree(home)

    def test_an_engine_from_an_archive_links_the_instance_and_is_not_synced(self):
        """support-4 dry run #1: no .git in the engine is no failure (update.py says the same); the instance's
        folders are still linked into it, and only the instance is synced."""
        home = tempfile.mkdtemp(prefix="家 ")
        eng, inst = os.path.join(home, "engine"), os.path.join(home, "instance")
        os.makedirs(os.path.join(inst, "knowledge"))
        os.makedirs(os.path.join(inst, "memory"))
        os.makedirs(os.path.join(inst, ".git"))  # sync_repo is a stand-in here
        os.makedirs(os.path.join(eng, "altention"))
        with open(os.path.join(inst, "plugins.json"), "w") as f:
            json.dump({"format": 1, "sync": {"owns": ["knowledge"]}}, f)
        synced = []
        with mock.patch.dict(os.environ, {"HOME": home}), mock.patch.object(config, "ENGINE", eng), \
                mock.patch.object(config, "instance_dir", return_value=inst), \
                mock.patch.object(config, "this_machine", return_value="host-a"), \
                mock.patch.object(sync, "not_this_engine", return_value=""), \
                mock.patch.object(sync, "compose_global", return_value="composed"), \
                mock.patch.object(sync, "sync_repo", side_effect=lambda path, *a, **k: synced.append(path) or
                                  {"at": 1, "ok": True, "error": None}):
            st = sync.run()
        self.assertEqual(synced, [inst], "the instance only")
        self.assertEqual((st["engine"]["ok"], st["engine"]["checkout"]), (True, False))
        for name in ("knowledge", "memory"):
            self.assertEqual(os.readlink(os.path.join(eng, name)), os.path.join(inst, name))
        self.assertTrue(os.path.exists(os.path.join(home, ".local", "state", "altention", "last_ok-sync")), "a good sync")
        shutil.rmtree(home)


if __name__ == "__main__":
    unittest.main()


class Stuck(unittest.TestCase):
    def test_any_failure_for_two_hours_is_told_once(self):
        home = tempfile.mkdtemp()
        failing = {"at": 0, "ok": False, "error": "fetch 失敗：network unreachable"}
        told = []
        with mock.patch.dict(os.environ, {"HOME": home}), mock.patch.object(config, "instance_dir", return_value=None), \
                mock.patch.object(config, "this_machine", return_value="host-a"), \
                mock.patch.object(sync, "is_checkout", return_value=True), \
                mock.patch.object(sync, "tell", side_effect=told.append):
            for at, result in ((1000, failing), (8300, failing), (9000, failing), (9500, {"at": 0, "ok": True, "error": None}),
                               (10000, failing)):
                with mock.patch.object(sync, "sync_repo", return_value=dict(result, at=at)):
                    sync.run()
        self.assertEqual(len(told), 1, "once after two hours; a success resets it, a new failure starts over")
        shutil.rmtree(home)

    def test_a_rebase_nobody_owns_is_told_once_after_two_hours(self):
        home = tempfile.mkdtemp()
        stuck = {"at": 0, "ok": False, "error": "有人正在這個資料夾手動 rebase", "stuck": True}  # a failure like any other
        told = []
        with mock.patch.dict(os.environ, {"HOME": home}), mock.patch.object(config, "instance_dir", return_value=None), \
                mock.patch.object(config, "this_machine", return_value="host-a"), \
                mock.patch.object(sync, "is_checkout", return_value=True), \
                mock.patch.object(sync, "tell", side_effect=told.append):
            for at in (1000, 1000 + 3600, 1000 + 7300, 1000 + 9000):
                with mock.patch.object(sync, "sync_repo", return_value=dict(stuck, at=at)):
                    sync.run()
        self.assertEqual(len(told), 1, "told once, after two hours")
        self.assertIn("host-a", told[0])
        shutil.rmtree(home)


class Adopt(unittest.TestCase):
    """Stage 2.3b: the engine's knowledge/ once the instance has one."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.environ.update({"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com", "GIT_COMMITTER_NAME": "t",
                           "GIT_COMMITTER_EMAIL": "t@example.com"})
        self.engine = os.path.join(self.tmp, "engine")
        self.instance = os.path.join(self.tmp, "instance")
        write(os.path.join(self.engine, "knowledge", "README.md"), "engine readme\n")
        write(os.path.join(self.engine, "knowledge", "坑 gotchas.md"), "old\n")
        sh(self.engine, "git", "init", "-q")
        sh(self.engine, "git", "add", "-A")
        sh(self.engine, "git", "commit", "-qm", "init")
        write(os.path.join(self.instance, "knowledge", "README.md"), "engine readme\n")
        write(os.path.join(self.instance, "knowledge", "坑 gotchas.md"), "old\n")
        write(os.path.join(self.instance, "plugins.json"), json.dumps({"format": 1, "sync": {"owns": ["knowledge", "docs/refactor"]}}))
        self.p = [mock.patch.object(config, "ENGINE", self.engine),
                  mock.patch.object(sync, "status_path", lambda: os.path.join(self.tmp, "cache", "sync.json"))]
        for p in self.p:
            p.start()

    def tearDown(self):
        for p in self.p:
            p.stop()
        shutil.rmtree(self.tmp)

    def test_uncommitted_edits_go_to_the_instance_and_the_engine_is_put_back(self):
        write(os.path.join(self.engine, "knowledge", "坑 gotchas.md"), "edited here\n")
        write(os.path.join(self.engine, "knowledge", "new note.md"), "new\n")
        state, twice = sync.adopt(self.instance, "knowledge", "host-a", lambda t: None)
        self.assertEqual(state, "tracked")
        self.assertEqual(read(os.path.join(self.instance, "knowledge", "new note.md")), "new\n")
        self.assertEqual(read(os.path.join(self.instance, "knowledge", "坑 gotchas.host-a.md")), "edited here\n")
        self.assertEqual(twice, [os.path.join("knowledge", "坑 gotchas.host-a.md")])
        self.assertEqual(sh(self.engine, "git", "status", "--porcelain").stdout, "", "the engine is as committed")

    def test_staged_edits_are_put_back_too(self):
        write(os.path.join(self.engine, "knowledge", "坑 gotchas.md"), "staged here\n")
        sh(self.engine, "git", "add", "-A")
        state, twice = sync.adopt(self.instance, "knowledge", "host-a", lambda t: None)
        self.assertEqual(state, "tracked")
        self.assertEqual(read(os.path.join(self.instance, "knowledge", "坑 gotchas.host-a.md")), "staged here\n")
        self.assertEqual(sh(self.engine, "git", "status", "--porcelain").stdout, "", "index and tree as committed")
        self.assertEqual(sync.adopt(self.instance, "knowledge", "host-a", lambda t: None), ("tracked", []), "once only")

    def test_after_git_drops_it_leftovers_move_and_a_link_stands_there(self):
        sh(self.engine, "git", "rm", "-rq", "knowledge")
        sh(self.engine, "git", "commit", "-qm", "gone")
        write(os.path.join(self.engine, "knowledge", ".DS_Store"), "x")  # a file git never knew stays behind
        state, _ = sync.adopt(self.instance, "knowledge", "host-a", lambda t: None)
        self.assertEqual(state, "link")
        self.assertEqual(os.path.realpath(os.path.join(self.engine, "knowledge")),
                         os.path.realpath(os.path.join(self.instance, "knowledge")))
        self.assertTrue(os.path.exists(os.path.join(self.instance, "knowledge", ".DS_Store")))
        self.assertFalse(os.path.exists(os.path.join(self.engine, "knowledge.DS_Store")))
        self.assertEqual(sh(self.engine, "git", "status", "--porcelain").stdout, "?? knowledge\n",
                         "only the link (the engine's .gitignore ignores it after 2.3b-2), nothing renamed aside")

    def test_no_instance_copy_yet_leaves_the_engine_alone(self):
        shutil.rmtree(os.path.join(self.instance, "knowledge"))
        write(os.path.join(self.engine, "knowledge", "坑 gotchas.md"), "edited\n")
        self.assertEqual(sync.adopt(self.instance, "knowledge", "host-a", lambda t: None), ("engine", []))
        self.assertEqual(read(os.path.join(self.engine, "knowledge", "坑 gotchas.md")), "edited\n")


class AdoptSwitch(Adopt):
    """Stage 2.3b's fixes: the switch, unpushed commits, files committed after the engine gave the path up."""

    def test_switch_off_takes_our_link_away_and_the_engine_is_used_again(self):
        shutil.rmtree(os.path.join(self.engine, "knowledge"))
        os.symlink(os.path.join(self.instance, "knowledge"), os.path.join(self.engine, "knowledge"))
        shutil.rmtree(os.path.join(self.instance, "knowledge"))  # undo-2.3b removed it from the instance
        self.assertEqual(sync.adopt(self.instance, "knowledge", "host-a", lambda t: None), ("engine", []))
        self.assertFalse(os.path.lexists(os.path.join(self.engine, "knowledge")))

    def test_unpushed_knowledge_commits_go_to_the_instance_and_are_taken_back(self):
        origin = os.path.join(self.tmp, "origin.git")
        sh(self.tmp, "git", "init", "-q", "--bare", origin)
        sh(self.engine, "git", "remote", "add", "origin", origin)
        sh(self.engine, "git", "push", "-q", "-u", "origin", "HEAD:main")
        sh(self.engine, "git", "branch", "-q", "-u", "origin/main")
        write(os.path.join(self.engine, "knowledge", "睡著時寫的 note.md"), "late\n")
        sh(self.engine, "git", "add", "-A")
        sh(self.engine, "git", "commit", "-qm", "sync: memory/knowledge @ mac")
        state, _ = sync.adopt(self.instance, "knowledge", "host-a", lambda t: None)
        self.assertEqual(state, "tracked")
        self.assertEqual(read(os.path.join(self.instance, "knowledge", "睡著時寫的 note.md")), "late\n")
        self.assertEqual(sh(self.engine, "git", "rev-list", "--count", "@{u}..HEAD").stdout.strip(), "0")
        self.assertEqual(sh(self.engine, "git", "status", "--porcelain").stdout, "")

    def test_unpushed_commits_touching_code_are_left_alone(self):
        origin = os.path.join(self.tmp, "origin.git")
        sh(self.tmp, "git", "init", "-q", "--bare", origin)
        sh(self.engine, "git", "remote", "add", "origin", origin)
        sh(self.engine, "git", "push", "-q", "-u", "origin", "HEAD:main")
        sh(self.engine, "git", "branch", "-q", "-u", "origin/main")
        write(os.path.join(self.engine, "knowledge", "n.md"), "n\n")
        write(os.path.join(self.engine, "code.py"), "x = 1\n")
        sh(self.engine, "git", "add", "-A")
        sh(self.engine, "git", "commit", "-qm", "mixed")
        sync.adopt(self.instance, "knowledge", "host-a", lambda t: None)
        self.assertEqual(read(os.path.join(self.instance, "knowledge", "n.md")), "n\n", "copied all the same")
        self.assertEqual(sh(self.engine, "git", "rev-list", "--count", "@{u}..HEAD").stdout.strip(), "1")

    def test_files_committed_after_the_engine_gave_the_path_up_are_handed_over(self):
        sh(self.engine, "git", "rm", "-rq", "knowledge")
        write(os.path.join(self.engine, ".gitignore"), "/knowledge\n")
        sh(self.engine, "git", "add", "-A")
        sh(self.engine, "git", "commit", "-qm", "2.3b-2")
        write(os.path.join(self.engine, "knowledge", "晚到 late.md"), "late\n")  # another machine's late commit
        sh(self.engine, "git", "add", "-f", "knowledge")
        sh(self.engine, "git", "commit", "-qm", "late")
        state, _ = sync.adopt(self.instance, "knowledge", "host-a", lambda t: None)
        self.assertEqual(state, "link")
        self.assertEqual(read(os.path.join(self.instance, "knowledge", "晚到 late.md")), "late\n")
        self.assertEqual(sh(self.engine, "git", "ls-files", "knowledge").stdout, "")
        self.assertIn("handed over", sh(self.engine, "git", "log", "-1", "--format=%s").stdout)
        self.assertEqual(read(os.path.join(self.engine, "knowledge", "README.md")), "engine readme\n", "README readable")
        self.assertTrue(os.listdir(os.path.join(self.tmp, "cache", "before-instance")), "the old folder is kept aside")
        self.assertEqual(sh(self.engine, "git", "status", "--porcelain").stdout, "")


class AdoptList(Adopt):
    """The switch is plugins.json's sync.owns; unpushed commits are taken back only whole, and kept as a ref."""

    def remote(self):
        origin = os.path.join(self.tmp, "origin.git")
        sh(self.tmp, "git", "init", "-q", "--bare", origin)
        sh(self.engine, "git", "remote", "add", "origin", origin)
        sh(self.engine, "git", "push", "-q", "-u", "origin", "HEAD:main")
        sh(self.engine, "git", "branch", "-q", "-u", "origin/main")

    def test_a_commit_touching_knowledge_and_docs_is_copied_whole_before_it_goes(self):
        write(os.path.join(self.engine, "docs", "refactor", "DESIGN.md"), "design\n")
        sh(self.engine, "git", "add", "-A")
        sh(self.engine, "git", "commit", "-qm", "design")
        write(os.path.join(self.instance, "docs", "refactor", "DESIGN.md"), "design\n")
        self.remote()
        write(os.path.join(self.engine, "knowledge", "k.md"), "k\n")
        write(os.path.join(self.engine, "docs", "refactor", "DESIGN.md"), "design, edited\n")
        sh(self.engine, "git", "add", "-A")
        sh(self.engine, "git", "commit", "-qm", "docs: both")
        with mock.patch.object(config, "this_machine", return_value="host-a"):
            sync.adopt_all(self.instance, lambda t: None)
        self.assertEqual(read(os.path.join(self.instance, "knowledge", "k.md")), "k\n")
        self.assertEqual(read(os.path.join(self.instance, "docs", "refactor", "DESIGN.host-a.md")), "design, edited\n")
        self.assertEqual(sh(self.engine, "git", "rev-list", "--count", "@{u}..HEAD").stdout.strip(), "0")
        refs = sync.adopted_refs(sync.Repo(self.engine))
        self.assertEqual(len(refs), 1)
        self.assertEqual(sh(self.engine, "git", "log", "-1", "--format=%s", refs[0]).stdout.strip(), "docs: both")

    def test_a_commit_touching_a_path_the_instance_does_not_hold_stays(self):
        self.remote()
        write(os.path.join(self.engine, "knowledge", "k.md"), "k\n")
        write(os.path.join(self.engine, "memory", "m.md"), "m\n")  # the instance has no memory/ here
        sh(self.engine, "git", "add", "-A")
        sh(self.engine, "git", "commit", "-qm", "both")
        sync.adopt_all(self.instance, lambda t: None)
        self.assertEqual(read(os.path.join(self.instance, "knowledge", "k.md")), "k\n", "copied all the same")
        self.assertEqual(sh(self.engine, "git", "rev-list", "--count", "@{u}..HEAD").stdout.strip(), "1")
        self.assertEqual(sync.adopted_refs(sync.Repo(self.engine)), [])

    def test_a_folder_the_list_does_not_name_goes_back_to_the_engine(self):
        sh(self.instance, "git", "init", "-q")
        sync.set_owns(self.instance, ["knowledge"], False)  # undo-2.3b took it out of the list
        write(os.path.join(self.instance, "knowledge", "回來 back.md"), "pushed after the undo\n")
        self.assertEqual(sync.adopt(self.instance, "knowledge", "host-a", lambda t: None), ("engine", []))
        self.assertTrue(os.path.isdir(os.path.join(self.instance, "knowledge")), "first sighting: only written down")
        with mock.patch.object(sync, "HANDBACK_AFTER", 0):
            state, _ = sync.adopt(self.instance, "knowledge", "host-a", lambda t: None)
        self.assertEqual(state, "engine")
        self.assertEqual(read(os.path.join(self.engine, "knowledge", "回來 back.md")), "pushed after the undo\n")
        self.assertFalse(os.path.isdir(os.path.join(self.instance, "knowledge")), "the switch stays off")
        self.assertTrue(os.listdir(os.path.join(self.tmp, "cache", "before-instance")))

    def test_an_unreadable_list_moves_nothing(self):
        write(os.path.join(self.instance, "plugins.json"), "{broken")
        write(os.path.join(self.engine, "knowledge", "坑 gotchas.md"), "edited\n")
        states, _ = sync.adopt_all(self.instance, lambda t: None)
        self.assertTrue(states["knowledge"].startswith("error"))
        self.assertEqual(read(os.path.join(self.engine, "knowledge", "坑 gotchas.md")), "edited\n")
        self.assertTrue(os.path.isdir(os.path.join(self.instance, "knowledge")))


class HandBackLock(AdoptList):
    def test_seen_once_and_then_listed_is_never_handed_back(self):
        sh(self.instance, "git", "init", "-q")
        sync.set_owns(self.instance, ["knowledge"], False)
        sync.adopt(self.instance, "knowledge", "host-a", lambda t: None)  # a pull halfway: seen once
        sync.set_owns(self.instance, ["knowledge"], True)  # the pull finished
        with mock.patch.object(sync, "HANDBACK_AFTER", 0):
            sync.adopt(self.instance, "knowledge", "host-a", lambda t: None)
        self.assertTrue(os.path.isfile(os.path.join(self.instance, "knowledge", "README.md")))

    def test_a_folder_not_yet_listed_is_left_while_the_instance_is_busy(self):
        sh(self.instance, "git", "init", "-q")
        sync.set_owns(self.instance, ["knowledge"], False)  # a checkout halfway: folder written, list not yet
        with mock.patch.object(sync, "HANDBACK_AFTER", 0):
            sync.adopt(self.instance, "knowledge", "host-a", lambda t: None)  # seen once already
            with sync.locked(sync.Repo(self.instance)):  # the instance's sync (or prepare) holds its lock
                state, _ = sync.adopt(self.instance, "knowledge", "host-a", lambda t: None)
        self.assertEqual(state, "engine")
        self.assertTrue(os.path.isfile(os.path.join(self.instance, "knowledge", "README.md")), "left where it is")



class EngineNotPushed(unittest.TestCase):
    """C-31: the sync pulls the engine and never pushes it (a public engine's fork is public); the instance is
    pushed as before."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="家 ")
        os.environ.update({"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com", "GIT_COMMITTER_NAME": "t",
                           "GIT_COMMITTER_EMAIL": "t@example.com"})
        self.home = os.path.join(self.tmp, "home")
        os.makedirs(self.home)
        self.repos = {}
        for name in ("engine", "instance"):
            bare = os.path.join(self.tmp, name + ".git")
            sh(self.tmp, "git", "init", "-q", "--bare", bare)
            sh(bare, "git", "symbolic-ref", "HEAD", "refs/heads/main")
            work = os.path.join(self.tmp, name)
            sh(self.tmp, "git", "clone", "-q", bare, work)
            sh(work, "git", "checkout", "-q", "-b", "main")
            write(os.path.join(work, "README.md"), name + "\n")
            sh(work, "git", "add", "-A")
            sh(work, "git", "commit", "-qm", "init")
            sh(work, "git", "push", "-q", "-u", "origin", "main")
            self.repos[name] = (bare, work)
        self.engine, self.instance = self.repos["engine"][1], self.repos["instance"][1]
        write(os.path.join(self.instance, "plugins.json"), json.dumps({"format": 1, "core": {"update": "branch"}}))
        sh(self.instance, "git", "add", "-A")
        sh(self.instance, "git", "commit", "-qm", "settings")
        sh(self.instance, "git", "push", "-q")

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def head(self, name):
        return sh(self.repos[name][0], "git", "rev-parse", "main").stdout.strip()

    def run_sync(self, instance=True):
        where = mock.patch.object(config, "instance_dir", return_value=self.instance) if instance else \
            mock.patch.object(config, "instance_dir", return_value=None)
        with mock.patch.dict(os.environ, {"HOME": self.home}), mock.patch.object(config, "ENGINE", self.engine), where, \
                mock.patch.object(config, "this_machine", return_value="host-a"), \
                mock.patch.object(sync, "not_this_engine", return_value=""), \
                mock.patch.object(sync, "update_mode", return_value=("branch", None)), \
                mock.patch.object(sync, "compose_global", return_value="composed"):
            return sync.run()

    def test_a_skill_the_update_brings_gets_its_link(self):
        """manager 2026-10-16: an update brought skills/tour and every machine said 「缺少 skill：tour」, since no
        path that moves the engine runs install.sh. The sync's round makes the missing link, whether this round pulled
        it (standing in) or update.py did before (nothing new to pull)."""
        other = os.path.join(self.tmp, "other")
        sh(self.tmp, "git", "clone", "-q", "-b", "main", self.repos["engine"][0], other)
        write(os.path.join(other, "skills", "newone", "SKILL.md"), "---\nname: newone\n---\n")
        sh(other, "git", "add", "-A")
        sh(other, "git", "commit", "-qm", "a new skill")
        sh(other, "git", "push", "-q")
        link = os.path.join(self.home, ".claude", "skills", "newone")
        st = self.run_sync()
        self.assertEqual(os.readlink(link), os.path.join(self.engine, "skills", "newone"))
        self.assertEqual(st["skills"], {"made": ["newone"]})
        os.remove(link)  # update.py moved the engine: this round pulls nothing and still links it
        st = self.run_sync()
        self.assertEqual((os.readlink(link), st["skills"]), (os.path.join(self.engine, "skills", "newone"), {"made": ["newone"]}))
        self.assertEqual(self.run_sync()["skills"], {"made": []})

    def test_a_worktrees_round_links_nothing(self):
        write(os.path.join(self.engine, "skills", "newone", "SKILL.md"), "x\n")
        with mock.patch.object(sync, "not_this_engine", return_value="a worktree"), \
                mock.patch.dict(os.environ, {"HOME": self.home}), mock.patch.object(config, "ENGINE", self.engine):
            self.assertEqual(sync.new_skills(lambda t: None), {"skipped": "a worktree"})
        self.assertFalse(os.path.lexists(os.path.join(self.home, ".claude", "skills", "newone")))

    def test_a_commit_made_here_is_not_pushed_the_instance_is(self):
        other = os.path.join(self.tmp, "other")  # someone else moved the engine's upstream
        sh(self.tmp, "git", "clone", "-q", "-b", "main", self.repos["engine"][0], other)
        write(os.path.join(other, "up.txt"), "up\n")
        sh(other, "git", "add", "-A")
        sh(other, "git", "commit", "-qm", "upstream")
        sh(other, "git", "push", "-q")
        upstream = self.head("engine")
        write(os.path.join(self.engine, "local.py"), "x = 1\n")  # a commit here that may hold private values
        sh(self.engine, "git", "add", "-A")
        sh(self.engine, "git", "commit", "-qm", "local")
        write(os.path.join(self.instance, "knowledge", "note.md"), "note\n")
        st = self.run_sync()
        self.assertEqual(self.head("engine"), upstream, "the engine's upstream got nothing from here")
        self.assertTrue(os.path.exists(os.path.join(self.engine, "up.txt")), "the engine was still pulled")
        self.assertEqual((st["engine"]["ok"], st["engine"]["ahead"]), (True, 1))
        self.assertIn("knowledge/note.md", sh(self.repos["instance"][0], "git", "ls-tree", "-r", "--name-only", "main").stdout,
                      "the instance is pushed as before")
        self.assertEqual(st["instance"].get("ahead"), None)
        with open(os.path.join(self.home, ".local", "state", "altention", "sync.json")) as f:
            self.assertEqual(json.load(f)["engine"]["ahead"], 1, "written down for doctor")

    def test_no_instance_yet_the_engines_knowledge_is_committed_and_stays_here(self):
        write(os.path.join(self.engine, "knowledge", "note.md"), "note\n")
        before = self.head("engine")
        st = self.run_sync(instance=False)
        self.assertEqual(sh(self.engine, "git", "status", "--porcelain").stdout, "", "committed here")
        self.assertEqual(self.head("engine"), before, "not pushed")
        self.assertEqual((st["engine"]["ok"], st["engine"]["ahead"]), (True, 1))

    def test_level_with_the_upstream_says_none_ahead(self):
        st = self.run_sync()
        self.assertEqual((st["engine"]["ok"], st["engine"]["ahead"]), (True, 0))


class DataRepos(unittest.TestCase):
    """C-26: a plugin's data folder marked sync "timer" (the secretary's) is committed, updated and pushed by the
    sync like the instance, under its own lock; one with no origin is left alone."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="家 ")
        os.environ.update({"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com", "GIT_COMMITTER_NAME": "t",
                           "GIT_COMMITTER_EMAIL": "t@example.com"})
        self.home = os.path.join(self.tmp, "home")
        os.makedirs(self.home)
        self.env = mock.patch.dict(os.environ, {"HOME": self.home})
        self.env.start()
        self.bare = os.path.join(self.tmp, "planner.git")
        sh(self.tmp, "git", "init", "-q", "--bare", self.bare)
        sh(self.bare, "git", "symbolic-ref", "HEAD", "refs/heads/main")
        self.folder = os.path.join(self.tmp, "data", "planner")
        os.makedirs(self.folder)
        sh(self.folder, "git", "init", "-q")
        sh(self.folder, "git", "checkout", "-q", "-b", "main")
        write(os.path.join(self.folder, "plan.md"), "# 計畫\n")
        sh(self.folder, "git", "add", "-A")
        sh(self.folder, "git", "commit", "-qm", "first")
        self.data = mock.patch.object(sync, "timer_data", return_value=[("planner/reminders", self.folder)])
        self.data.start()

    def tearDown(self):
        self.data.stop()
        self.env.stop()
        shutil.rmtree(self.tmp)

    def on_remote(self):
        return sh(self.bare, "git", "ls-tree", "-r", "--name-only", "main").stdout.split()

    def attach(self):
        """What ops/remote.py attach leaves: origin and our pre-push hook."""
        sh(self.folder, "git", "remote", "add", "origin", self.bare)
        sys.path.insert(0, os.path.join(ENGINE, "ops"))
        import remote
        self.assertTrue(remote.put_hook(self.folder)[0])

    def test_our_hook_mark_is_remote_pys(self):
        sys.path.insert(0, os.path.join(ENGINE, "ops"))
        import remote
        self.assertEqual(sync.OUR_HOOK, remote.HOOK_MARK)

    def test_the_owners_own_repo_with_an_origin_and_no_hook_is_left(self):
        """Manager 2026-10-16: only a repo we took on (our hook in) is synced; a setting pointing a plugin's data at
        the owner's own repo must not have it committed and pushed every 15 minutes."""
        sh(self.folder, "git", "remote", "add", "origin", self.bare)
        write(os.path.join(self.folder, "plan.md"), "- 半寫的\n", "a")
        r = sync.sync_data("host-a", lambda t: None)["planner/reminders"]
        self.assertIn("沒接上", r["note"])
        self.assertEqual(self.on_remote(), [], "nothing pushed")
        self.assertIn("plan.md", sh(self.folder, "git", "status", "--porcelain").stdout, "nothing committed")
        self.assertEqual(sh(self.folder, "git", "rev-list", "--count", "HEAD").stdout.strip(), "1")

    def test_no_origin_is_left_alone(self):
        write(os.path.join(self.folder, "plan.md"), "- 新的一項\n", "a")
        out = sync.sync_data("host-a", lambda t: None)
        self.assertEqual(out["planner/reminders"]["note"], "沒有設定遠端，只留在這台")
        self.assertIn("plan.md", sh(self.folder, "git", "status", "--porcelain").stdout, "not committed by the sync")

    def test_an_origin_without_upstream_is_pushed_and_tracked_then_kept_up(self):
        self.attach()
        write(os.path.join(self.folder, "reminders.json"), "{\"items\": []}\n")  # written by another channel's Claude
        r = sync.sync_data("host-a", lambda t: None)["planner/reminders"]
        self.assertTrue(r["ok"], r)
        self.assertEqual(sorted(self.on_remote()), ["plan.md", "reminders.json"])
        self.assertEqual(sh(self.folder, "git", "rev-parse", "--abbrev-ref", "@{u}").stdout.strip(), "origin/main")
        self.assertIn("sync: planner/reminders @ host-a", sh(self.folder, "git", "log", "-1", "--format=%s").stdout)
        other = os.path.join(self.tmp, "other")  # the same folder on another machine
        sh(self.tmp, "git", "clone", "-q", self.bare, other)
        write(os.path.join(other, "plan.md"), "- 別台加的\n", "a")
        sh(other, "git", "commit", "-qam", "there")
        sh(other, "git", "push", "-q")
        write(os.path.join(self.folder, "reminders.json"), "{\"items\": [1]}\n")
        r = sync.sync_data("host-a", lambda t: None)["planner/reminders"]
        self.assertTrue(r["ok"], r)
        self.assertIn("別台加的", read(os.path.join(self.folder, "plan.md")), "pulled")
        sh(other, "git", "pull", "-q")
        self.assertEqual(read(os.path.join(other, "reminders.json")), "{\"items\": [1]}\n", "diverged, met, pushed")

    def test_a_remote_with_the_branch_already_is_tracked_and_taken(self):
        self.attach()
        sh(self.folder, "git", "push", "-q", "origin", "main")  # pushed once without -u
        other = os.path.join(self.tmp, "other")
        sh(self.tmp, "git", "clone", "-q", self.bare, other)
        write(os.path.join(other, "notes.md"), "n\n")
        sh(other, "git", "add", "-A")
        sh(other, "git", "commit", "-qm", "there")
        sh(other, "git", "push", "-q")
        r = sync.sync_data("host-a", lambda t: None)["planner/reminders"]
        self.assertTrue(r["ok"], r)
        self.assertTrue(os.path.exists(os.path.join(self.folder, "notes.md")))

    def test_a_held_lock_stops_this_repo_only(self):
        self.attach()
        write(os.path.join(self.folder, "plan.md"), "- 秘書正在寫\n", "a")
        repo = sync.Repo(self.folder)
        with mock.patch.object(sync, "WAIT", 1), sync.locked(repo):  # the secretary's home-git commit
            r = sync.sync_data("host-a", lambda t: None)["planner/reminders"]
        self.assertFalse(r["ok"])
        self.assertIn("git 鎖", r["error"])
        self.assertEqual(self.on_remote(), [], "nothing pushed")
        self.assertIn("plan.md", sh(self.folder, "git", "status", "--porcelain").stdout, "nothing committed")

    def test_a_guard_that_says_no_stops_the_push(self):
        sh(self.folder, "git", "remote", "add", "origin", self.bare)
        r = sync.sync_repo(self.folder, ["."], "sync", lambda t: None, track=True, guard=lambda repo: "沒有推：公開的 repo")
        self.assertFalse(r["ok"])
        self.assertIn("公開的 repo", r["error"])
        self.assertEqual(self.on_remote(), [])

    def test_the_guard_runs_remote_py_in_the_repo(self):
        sh(self.folder, "git", "remote", "add", "origin", self.bare)
        repo = sync.Repo(self.folder)
        self.assertIsNone(sync.push_guard(repo), "a folder on this machine: private")
        fake = os.path.join(self.tmp, "eng")
        for code, want in ((10, "公開"), (1, None)):
            write(os.path.join(fake, "ops", "remote.py"),
                  "import sys\nprint('altention：沒有推：公開的 repo', file=sys.stderr)\nsys.exit(%d)\n" % code)
            with mock.patch.object(config, "ENGINE", fake):
                got = sync.push_guard(repo)
            if want is None:
                self.assertIsNone(got, "remote.py failing: our hook checks again, and lets it go as well")
            else:
                self.assertIn(want, got)

    def test_only_its_own_repo_is_synced(self):
        inner = os.path.join(self.folder, "sub")
        os.makedirs(inner)
        self.assertFalse(sync.own_repo(inner), "a folder inside another repo")
        self.assertTrue(sync.own_repo(self.folder))

    def test_timer_data_reads_the_plugins_on_here(self):
        sync_none = {"name": "x", "data": [{"name": "cache", "path": "~/c", "sync": "none"},
                                           {"name": "kept", "path": "{data}/kept"}]}  # no sync: "timer"
        with open(os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "planner",
                               "plugin.json")) as f:
            planner = json.load(f)
        rows = [("planner", "/p", planner, "on", ""), ("x", "/x", sync_none, "on", ""),
                ("off", "/o", {"data": [{"name": "d", "path": "/o/d"}]}, "disabled", ""), ("bad", "/b", None, "unknown", "")]
        self.data.stop()
        try:
            with mock.patch("altention.plugin.on_this_machine", return_value=(rows, [])), \
                    mock.patch.dict(os.environ, {"ALT_DATA": os.path.join(self.tmp, "data")}):
                got = sync.timer_data()
        finally:
            self.data.start()
        self.assertEqual(got, [("planner/reminders", self.folder), ("x/kept", os.path.join(self.tmp, "data", "kept"))])


class InstanceSettings(EngineNotPushed):
    """C-26: the instance's settings (plugins.json, machines.json) and backup/ are committed by the sync; a settings
    file that does not read is left out (every other machine would hold the instance back), and never removed."""

    def remote_tree(self):
        return sh(self.repos["instance"][0], "git", "ls-tree", "-r", "--name-only", "main").stdout.split()

    def remote_file(self, name):
        return sh(self.repos["instance"][0], "git", "show", "main:" + name).stdout

    def test_the_settings_and_backup_are_committed(self):
        write(os.path.join(self.instance, "plugins.json"), json.dumps({"format": 1, "core": {"update": "branch"},
                                                                       "plugins": {"planner": {"disabled_on": ["lap"]}}}))
        write(os.path.join(self.instance, "machines.json"), json.dumps({"format": 1, "machines": {"lap": {}}}))
        write(os.path.join(self.instance, "backup", "dcbot", "channels.json"), "{}\n")
        st = self.run_sync()
        self.assertTrue(st["instance"]["ok"], st["instance"])
        self.assertIn("machines.json", self.remote_tree())
        self.assertIn("backup/dcbot/channels.json", self.remote_tree())
        self.assertIn("disabled_on", self.remote_file("plugins.json"))

    def test_a_broken_settings_file_is_not_committed_and_said(self):
        write(os.path.join(self.instance, "plugins.json"), "{\"format\": 1, \"plugins\": ")  # half saved
        write(os.path.join(self.instance, "knowledge", "note.md"), "note\n")
        self.run_sync()
        self.assertEqual(json.loads(self.remote_file("plugins.json")), {"format": 1, "core": {"update": "branch"}})
        self.assertIn("knowledge/note.md", self.remote_tree(), "the rest still goes out")
        alerts = read(os.path.join(self.home, ".local", "state", "altention", "alerts"))
        self.assertIn("settings-broken", alerts)

    def test_a_settings_file_removed_here_is_not_removed_everywhere(self):
        os.remove(os.path.join(self.instance, "plugins.json"))
        st = self.run_sync()
        self.assertIn("plugins.json", self.remote_tree())
        self.assertFalse(st["instance"]["ok"])
        self.assertIn("plugins.json", st["instance"]["error"])
