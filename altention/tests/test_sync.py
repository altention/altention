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
import tempfile
import unittest
from unittest import mock

from altention import config, sync


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

