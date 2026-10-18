"""ops/instance.py: seed an empty instance repo from an engine, join it from two "machines" (two HOMEs)."""
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

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))
import instance  # noqa: E402
from altention import config, sync  # noqa: E402


def sh(cwd, *args):
    return subprocess.run(list(args), cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(text)


class Rig(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.environ.update({"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com", "GIT_COMMITTER_NAME": "t",
                           "GIT_COMMITTER_EMAIL": "t@example.com"})
        self.url = os.path.join(self.tmp, "my-instance.git")
        sh(self.tmp, "git", "init", "-q", "--bare", self.url)
        self.engine = os.path.join(self.tmp, "engine")
        write(os.path.join(self.engine, "memory", "MEMORY.md"), "- one\n")
        write(os.path.join(self.engine, "memory", "中文 記憶.md"), "x\n")
        write(os.path.join(self.engine, "ops", "machines.json"), json.dumps({"machines": {"host-a": {"ssh": ["local"]}}}))
        write(os.path.join(self.engine, "ops", "ssh_config"), "Host host-b\n")

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def machine(self, name):
        home = os.path.join(self.tmp, name)
        os.makedirs(home, exist_ok=True)  # the same machine may be visited twice
        return [mock.patch.dict(os.environ, {"HOME": home, "ALT_INSTANCE": "", "ALT_ROOT": ""}),
                mock.patch.object(instance, "ENGINE", self.engine), mock.patch.object(config, "ENGINE", self.engine),
                mock.patch.object(config, "hostname", return_value=name)], home

    def run_on(self, name, fn):
        patches, home = self.machine(name)
        for p in patches:
            p.start()
        try:
            os.environ.pop("ALT_INSTANCE")
            os.environ.pop("ALT_ROOT")
            return fn(home)
        finally:
            for p in patches:
                p.stop()


class SeedAndJoin(Rig):
    def test_seed_once_then_join_on_two_machines(self):
        def first(home):
            self.assertEqual(instance.seed(self.url), 0)
            self.assertEqual(instance.seed(self.url), 0, "again: leaves it as it is")
            inst = os.path.join(home, "altention", "instance")
            self.assertTrue(os.path.exists(os.path.join(inst, "memory", "中文 記憶.md")))
            self.assertEqual(json.load(open(os.path.join(inst, "machines.json")))["format"], 1)
            self.assertIn("merge=union", open(os.path.join(inst, ".gitattributes")).read())
        self.run_on("host-a", first)

        def second(home):
            self.assertEqual(instance.join(self.url), 0)
            self.assertEqual(instance.join(self.url), 0, "again: nothing changes")
            inst = os.path.realpath(os.path.join(home, "altention", "instance"))
            self.assertEqual(config.instance_dir(), inst)
            self.assertEqual(os.path.realpath(os.path.join(home, "altention", "engine")), os.path.realpath(self.engine))
            self.assertEqual(config.load("machines.json")[1], "instance")
        self.run_on("host-b", second)

    def test_after_join_the_next_sync_moves_memory_with_nothing_lost(self):
        self.run_on("host-a", lambda home: instance.seed(self.url))

        def b(home):
            local_mem = os.path.join(home, ".claude", "projects", sync.slug(os.path.realpath(self.engine)), "memory")
            write(os.path.join(local_mem, "這台才有.md"), "only on b\n")  # a real folder, as on a machine never linked
            write(os.path.join(local_mem, "MEMORY.md"), "- b's own index\n")
            instance.join(self.url)
            with mock.patch.object(sync, "sync_repo", return_value={"ok": True, "error": None, "at": 0}):
                st = sync.run()
            inst_mem = os.path.join(config.instance_dir(), "memory")
            self.assertEqual(os.path.realpath(local_mem), os.path.realpath(inst_mem))
            self.assertTrue(os.path.exists(os.path.join(inst_mem, "這台才有.md")))
            self.assertIn("b's own index", open(os.path.join(inst_mem, "MEMORY.host-b.md")).read())
            self.assertIn("MEMORY.host-b.md", st["links"]["twice"])
        self.run_on("host-b", b)

    def test_leave_puts_everything_back_and_keeps_new_memory(self):
        self.run_on("host-a", lambda home: instance.seed(self.url))

        def b(home):
            os.makedirs(os.path.join(home, ".ssh"))
            write(os.path.join(home, ".ssh", "config"), "Include %s/ops/ssh_config\nHost mine\n" % self.engine)
            link = os.path.join(home, ".claude", "projects", sync.slug(os.path.realpath(self.engine)), "memory")
            os.makedirs(os.path.dirname(link))
            os.symlink(os.path.join(self.engine, "memory"), link)
            instance.join(self.url)
            with mock.patch.object(sync, "sync_repo", return_value={"ok": True, "error": None, "at": 0}):
                sync.run()
            inst = config.instance_dir()
            write(os.path.join(inst, "memory", "寫在實例裡的.md"), "new\n")  # written after the move
            self.assertEqual(instance.leave(), 0)
            self.assertEqual(os.path.realpath(link), os.path.realpath(os.path.join(self.engine, "memory")))
            self.assertTrue(os.path.exists(os.path.join(self.engine, "memory", "寫在實例裡的.md")), "nothing written meanwhile is lost")
            self.assertIn("Include %s/ops/ssh_config" % self.engine, open(os.path.join(home, ".ssh", "config")).read())
            self.assertIn("Host mine", open(os.path.join(home, ".ssh", "config")).read())
            self.assertIsNone(config.instance_dir())
        self.run_on("host-b", b)

    def test_after_leave_join_needs_rejoin(self):
        self.run_on("host-a", lambda home: instance.seed(self.url))

        def b(home):
            instance.join(self.url)
            instance.leave()
            instance.join(self.url)  # what apply does
            self.assertIsNone(config.instance_dir(), "not quietly joined again")
            instance.join(self.url, rejoin=True)
            self.assertIsNotNone(config.instance_dir())
        self.run_on("host-b", b)

    def test_an_interrupted_seed_leaves_no_instance_and_can_run_again(self):
        def a(home):
            real = instance.git

            def push_fails(path, *args, **kw):
                if args[:1] == ("push",):
                    return subprocess.CompletedProcess(args, 1, "", "network")
                return real(path, *args, **kw)
            with mock.patch.object(instance, "git", push_fails):
                with self.assertRaises(SystemExit):
                    instance.seed(self.url)
            self.assertIsNone(config.instance_dir(), "half made: not this machine's instance")
            self.assertEqual(instance.seed(self.url), 0)
            self.assertIsNotNone(config.instance_dir())
        self.run_on("host-a", a)

    def test_install_sh_leaves_the_links_alone_once_there_is_an_instance(self):
        self.run_on("host-a", lambda home: instance.seed(self.url))

        def b(home):
            os.makedirs(os.path.join(home, ".ssh"))
            write(os.path.join(home, ".ssh", "config"), "Host mine\n")
            instance.join(self.url)
            with mock.patch.object(sync, "sync_repo", return_value={"ok": True, "error": None, "at": 0}):
                sync.run()
            links = {l: os.path.realpath(l) for l in sync.memory_links()}
            ssh_before = open(os.path.join(home, ".ssh", "config")).read()
            env = dict(os.environ, HOME=home, CHATLOG="0", SHELL="")  # no shell of the user's started (core.path)
            os.makedirs(os.path.join(self.engine, "skills"), exist_ok=True)
            shutil.copy(os.path.join(os.path.dirname(HERE), "install.sh"), os.path.join(self.engine, "install.sh"))
            for folder in ("altention", "ops"):  # the installer is ops/install.py since 3a.9
                shutil.copytree(os.path.join(os.path.dirname(HERE), folder), os.path.join(self.engine, folder),
                                ignore=shutil.ignore_patterns("__pycache__", "tests"), dirs_exist_ok=True)
            r = subprocess.run(["bash", os.path.join(self.engine, "install.sh"), "--links"], env=env, stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, universal_newlines=True)
            self.assertEqual(r.returncode, 0, r.stdout)
            self.assertIn("ok core.memory", r.stdout)
            self.assertEqual({l: os.path.realpath(l) for l in sync.memory_links()}, links, "memory links untouched")
            self.assertEqual(open(os.path.join(home, ".ssh", "config")).read(), ssh_before, "Include untouched")
        self.run_on("host-b", b)

    def test_after_leave_nothing_points_at_the_instance(self):
        self.run_on("host-a", lambda home: instance.seed(self.url))

        def b(home):
            instance.join(self.url)
            instance.leave()
            self.assertFalse(os.path.exists(os.path.join(home, ".config", "altention", "instance")), "no pointer an old engine trips on")
            self.assertFalse(os.path.exists(os.path.join(home, "altention", "instance")), "not found by default either")
            self.assertTrue([n for n in os.listdir(os.path.join(home, "altention")) if n.startswith("instance.left-")], "kept")
            self.assertEqual(config.load("machines.json")[1], "none")
        self.run_on("host-b", b)

    def test_a_join_cut_short_or_made_before_the_seed_recovers(self):
        def b(home):
            with self.assertRaises(SystemExit):
                instance.join(self.url)  # before the seed: nothing there yet
            self.assertFalse(os.path.exists(os.path.join(home, "altention", "instance")), "an empty clone is not left in place")
            os.makedirs(os.path.join(home, "altention", "instance.cloning", ".git"))  # a clone that was killed
        self.run_on("host-b", b)
        self.run_on("host-a", lambda home: instance.seed(self.url))

        def again(home):
            self.assertEqual(instance.join(self.url), 0)
            self.assertIsNotNone(config.instance_dir())
        self.run_on("host-b", again)

    def test_join_before_seed_refuses(self):
        def go(home):
            with self.assertRaises(SystemExit):
                instance.join(self.url)
        self.run_on("host-a", go)


class Private(Rig):
    """C-26: the instance holds the owner's memory and settings: 700 from the start, with a usual umask too."""

    def setUp(self):
        super().setUp()
        self.umask = os.umask(0o022)

    def tearDown(self):
        os.umask(self.umask)
        super().tearDown()

    def mode(self, path):
        return os.stat(path).st_mode & 0o777

    def test_init_join_and_one_already_there_are_700(self):
        def first(home):
            self.assertEqual(instance.init(), 0)
            inst = os.path.join(home, "altention", "instance")
            self.assertEqual(self.mode(inst), 0o700, "init")
            os.chmod(inst, 0o755)  # made by an older engine
            self.assertEqual(instance.init(), 0)
            self.assertEqual(self.mode(inst), 0o700, "init again: made private")
        self.run_on("host-a", first)
        self.run_on("host-c", lambda home: instance.seed(self.url))

        def second(home):
            self.assertEqual(instance.join(self.url), 0)
            inst = os.path.join(home, "altention", "instance")
            self.assertEqual(self.mode(inst), 0o700, "join")
            os.chmod(inst, 0o755)
            self.assertEqual(instance.join(self.url), 0)
            self.assertEqual(self.mode(inst), 0o700, "join again: made private")
        self.run_on("host-b", second)


class JoinHook(Rig):
    """C-25 (manager 2026-10-17): a joined instance gets ops/remote.py's pre-push hook, so its pushes are checked
    from the first one; put_hook's rules hold (core.hooksPath, someone else's hook: left and said)."""

    def setUp(self):
        super().setUp()
        import remote
        self.remote = remote
        self.run_on("host-a", lambda home: instance.seed(self.url))

    def join(self, name, before=None):
        out = io.StringIO()

        def go(home):
            if before:
                before(home)
            with redirect_stdout(out):
                self.assertEqual(instance.join(self.url), 0)
            return os.path.join(home, "altention", "instance")
        return self.run_on(name, go), out.getvalue()

    def test_join_puts_the_hook_in_and_again_leaves_it(self):
        inst, said = self.join("host-b")
        self.assertTrue(self.remote.has_hook(inst))
        self.assertIn("放好 pre-push hook", said)
        with open(os.path.join(inst, ".git", "hooks", "pre-push")) as f:
            first = f.read()
        inst, said = self.join("host-b")
        self.assertIn("pre-push hook 已經在", said)
        with open(os.path.join(inst, ".git", "hooks", "pre-push")) as f:
            self.assertEqual(f.read(), first)

    def test_an_instance_joined_before_gets_it_on_the_next_join(self):
        """apply.py runs join on every machine: the instances joined before this change get the hook that way."""
        def without(home):
            with redirect_stdout(io.StringIO()):
                instance.join(self.url)
            os.remove(os.path.join(home, "altention", "instance", ".git", "hooks", "pre-push"))
        self.run_on("host-b", without)
        inst, said = self.join("host-b")
        self.assertTrue(self.remote.has_hook(inst))

    def test_hooks_path_or_another_hook_is_left_and_said(self):
        shared = os.path.join(self.tmp, "shared-hooks")
        os.makedirs(shared)

        def global_hooks(home):
            write(os.path.join(home, ".gitconfig"), "[core]\n\thooksPath = %s\n" % shared)
        inst, said = self.join("host-c", global_hooks)
        self.assertFalse(self.remote.has_hook(inst))
        self.assertEqual(os.listdir(shared), [], "nothing put into the shared folder")
        self.assertIn("⚠", said)
        self.assertIn("core.hooksPath", said)
        self.assertIn("python3 ops/remote.py hook instance", said)
        self.assertEqual(said.count("推送前不會檢查"), 1, "said once (#code-review of bc09467)")

        def theirs(home):
            with redirect_stdout(io.StringIO()):
                instance.join(self.url)
            write(os.path.join(home, "altention", "instance", ".git", "hooks", "pre-push"), "#!/bin/sh\nexit 0\n")
        inst, said = self.join("host-d", theirs)
        with open(os.path.join(inst, ".git", "hooks", "pre-push")) as f:
            self.assertEqual(f.read(), "#!/bin/sh\nexit 0\n", "someone else's hook untouched")
        self.assertIn("已經有別的 pre-push hook", said)

    def test_init_speaks_of_the_backup(self):
        out = io.StringIO()

        def go(home):
            with redirect_stdout(out):
                instance.init()
        self.run_on("host-e", go)
        self.assertIn("電腦壞了救不回來", out.getvalue())
        self.assertIn("python3 ops/remote.py set instance", out.getvalue())
        self.assertNotIn("多台共用", out.getvalue())


class SeedChecks(Rig):
    """C-41: seed pushes the instance once: the URL passes ops/remote.py's checks first, and the seeded repo has the
    pre-push hook before that push."""

    def seed(self, name="host-a", before=None):
        out = io.StringIO()

        def go(home):
            if before:
                before(home)
            with redirect_stdout(out):
                try:
                    return instance.seed(self.url), os.path.join(home, "altention", "instance")
                except SystemExit as e:
                    return e.code, os.path.join(home, "altention", "instance")
        (rc, inst) = self.run_on(name, go)
        return rc, inst, out.getvalue()

    def test_the_seeded_instance_has_the_hook(self):
        import remote
        rc, inst, said = self.seed()
        self.assertEqual(rc, 0, said)
        self.assertTrue(remote.has_hook(inst))
        self.assertIn("放好 pre-push hook", said)
        self.assertEqual(sh(self.url, "git", "rev-parse", "main").returncode, 0, "pushed")

    def test_a_url_that_fails_a_check_is_neither_cloned_nor_pushed(self):
        import remote
        public = [("anonymous", "no", "這個 repo 是公開的（讀得到）：改成 private 再設，或換一個 private 的")]
        with mock.patch.object(remote, "check", return_value=public):
            rc, inst, said = self.seed()
        self.assertIn("沒有 seed", str(rc))
        self.assertIn("✗ 這個 repo 是公開的", said)
        self.assertFalse(os.path.exists(inst))
        self.assertFalse(os.path.exists(inst + ".seeding"))
        self.assertNotEqual(sh(self.url, "git", "rev-parse", "-q", "--verify", "main").returncode, 0, "nothing pushed")

    def test_the_engine_or_another_kind_of_data_is_refused_by_the_real_checks(self):
        work = os.path.join(self.tmp, "other.work")
        sh(self.tmp, "git", "clone", "-q", self.url, work)
        write(os.path.join(work, "notes.md"), "mine\n")
        sh(work, "git", "add", "-A")
        sh(work, "git", "commit", "-qm", "theirs")
        sh(work, "git", "push", "-q", "origin", "HEAD:main")
        rc, inst, said = self.seed()
        self.assertIn("沒有 seed", str(rc))
        self.assertFalse(os.path.exists(inst))

    def test_hooks_path_is_said_and_the_push_still_checked_once_by_seed(self):
        shared = os.path.join(self.tmp, "shared-hooks")
        os.makedirs(shared)
        rc, inst, said = self.seed(before=lambda home: write(os.path.join(home, ".gitconfig"), "[core]\n\thooksPath = %s\n" % shared))
        self.assertEqual(rc, 0, said)
        self.assertIn("core.hooksPath", said)
        self.assertEqual(os.listdir(shared), [])
        self.assertNotIn(".seeding", said, "#code-review-2 of c0d45eb: the folder they will find is named")
        self.assertIn(inst, said)

    def test_an_engine_without_memory_seeds_an_empty_one(self):
        """#code-review-2 of c0d45eb: the public engine has no memory/; seed ended in a traceback."""
        shutil.rmtree(os.path.join(self.engine, "memory"))
        rc, inst, said = self.seed()
        self.assertEqual(rc, 0, said)
        self.assertEqual(os.listdir(os.path.join(inst, "memory")), ["MEMORY.md"])
        self.assertEqual(sh(self.url, "git", "rev-parse", "-q", "--verify", "main:memory/MEMORY.md").returncode, 0, "pushed")


class TakeName(Rig):
    """C-45: a machine restored from the backups takes the name of the one it replaces (manager 2026-10-17)."""

    def setUp(self):
        super().setUp()
        self.run_on("old-box", lambda home: instance.seed(self.url))
        work = os.path.join(self.tmp, "inst.work")
        sh(self.tmp, "git", "clone", "-q", "-b", "main", self.url, work)
        settings = json.load(open(os.path.join(work, "plugins.json")))
        settings["plugins"] = {"usage": {"disabled_on": ["old-box"]}, "progress": {"on": "bot"},
                               "council": {"disabled_on": ["typo-box"]}}
        write(os.path.join(work, "plugins.json"), json.dumps(settings))
        write(os.path.join(work, "machines.json"), json.dumps({"format": 1, "machines": {
            "old-box": {"ssh": ["OLDBOX"]}, "server": {"ssh": ["SRV"]}}}))
        sh(work, "git", "add", "-A")
        sh(work, "git", "commit", "-qm", "usage off on old-box")
        sh(work, "git", "push", "-q", "origin", "HEAD:main")

    def on_new(self, fn):
        def go(home):
            with redirect_stdout(io.StringIO()):
                instance.join(self.url)
            out = io.StringIO()
            with redirect_stdout(out):
                got = fn(home)
            return got, out.getvalue()
        return self.run_on("new-box", go)

    def host(self, home):
        path = os.path.join(home, ".config", "altention", "host")
        return open(path).read() if os.path.exists(path) else None

    def test_names_from_the_settings_the_list_and_the_records(self):
        import remote
        logs = os.path.join(self.tmp, "logs.git")

        def go(home):
            with mock.patch.object(config, "setting", side_effect=lambda k, *a: logs if k == "sync.dirs.chatlog.remote" else None), \
                    mock.patch.object(remote, "tree_of", return_value=(["old-box/2026-10/x/meta.json", "lap/a/meta.json", "README.md"], None)):
                return instance.names()
        got, _ = self.on_new(go)
        self.assertEqual(dict(got), {"lap": ["對話紀錄"], "old-box": ["plugins.json", "machines.json", "對話紀錄"],
                                     "server": ["machines.json"], "typo-box": ["plugins.json"]},
                         "bot (a word, not a machine) left out")

    def test_take_name_and_the_plugins_turned_off_there_stay_off(self):
        def go(home):
            from altention import plugin
            before = {n: w for n, _, _, w, _ in plugin.on_this_machine()[0]}
            with mock.patch.object(instance, "answers", return_value=[]):
                rc = instance.take_name("old-box")
            after = {n: w for n, _, _, w, _ in plugin.on_this_machine()[0]}
            return rc, self.host(home), config.this_machine(), before.get("usage"), after.get("usage")
        (rc, host, me, before, after), said = self.on_new(go)
        self.assertEqual((rc, host, me), (0, "old-box\n", "old-box"), said)
        self.assertEqual((before, after), ("unknown", "disabled"),
                         "new-box is on no list: nothing placed; as old-box, usage stays off there")
        self.assertIn("這台改叫 old-box", said)
        self.assertIn("plugins.json", said)

    def test_the_old_machine_still_answering_is_refused(self):
        def go(home):
            with mock.patch.object(instance, "answers", return_value=["OLDBOX"]) as asked:
                rc = instance.take_name("old-box")
            return rc, self.host(home), asked.call_args[0][0]
        (rc, host, machine), said = self.on_new(go)
        self.assertEqual((rc, host), (1, None))
        self.assertIn("還連得上（ssh OLDBOX）", said)
        self.assertEqual(machine, {"ssh": ["OLDBOX"]}, "asked with that machine's own aliases")

    def test_a_name_already_there_is_never_changed_and_a_typo_is_refused(self):
        def go(home):
            write(os.path.join(home, ".config", "altention", "host"), "mine\n")
            first = instance.take_name("old-box")
            same = instance.take_name("mine")
            os.remove(os.path.join(home, ".config", "altention", "host"))
            with mock.patch.object(instance, "answers", return_value=[]):
                typo = instance.take_name("old-bx")
            return first, same, typo, self.host(home)
        (first, same, typo, host), said = self.on_new(go)
        self.assertEqual((first, same, typo, host), (1, 0, 1, None))
        self.assertIn("已經叫 mine", said)
        self.assertIn("沒有叫 old-bx 的機器", said)

    def test_a_name_only_in_the_settings_needs_the_flag(self):
        """#code-review-2 of b8b5387: a name in plugins.json alone (a typo, or a machine left off the list) has no
        alias to ask over SSH: refused unless --only-in-settings (manager 2026-10-17); names marks it."""
        def go(home):
            with mock.patch.object(instance, "answers", return_value=[]) as asked:
                first = instance.take_name("typo-box")
                host = self.host(home)
                out = io.StringIO()
                with redirect_stdout(out):
                    instance.main(["names", "--json"])
                then = instance.main(["take-name", "typo-box", "--only-in-settings"])
            return first, host, json.loads(out.getvalue()), then, self.host(home), asked.call_count
        (first, host, rows, then, after, asked), said = self.on_new(go)
        self.assertEqual((first, host), (1, None))
        self.assertIn("只出現在設定裡", said)
        self.assertIn("--only-in-settings", said)
        self.assertEqual({r["name"]: r["only_in_settings"] for r in rows}, {"old-box": False, "server": False, "typo-box": True})
        self.assertEqual((then, after), (0, "typo-box\n"))
        self.assertEqual(asked, 0, "no list entry: nothing to ask over SSH (the skill asks the person)")

    def test_answers_asks_each_alias_in_batch_mode(self):
        calls = []

        def fake(cmd, **kw):
            calls.append(cmd)
            return subprocess.CompletedProcess(cmd, 0 if cmd[-2] == "B" else 255)
        with mock.patch.object(instance.subprocess, "run", side_effect=fake):
            self.assertEqual(instance.answers({"ssh": ["A", "B", "local"]}), ["B"])
        self.assertEqual([c[-2] for c in calls], ["A", "B"], "local is not an alias")
        self.assertIn("BatchMode=yes", calls[0])


class Skeleton(unittest.TestCase):
    def test_a_new_instance_takes_this_machines_time_zone(self):
        with mock.patch.dict(os.environ, {"TZ": "Europe/London"}):
            self.assertEqual(instance.plugins_skeleton()["core"]["timezone"], "Europe/London")
        with mock.patch.dict(os.environ, {"TZ": ""}), \
                mock.patch.object(instance.os.path, "realpath", return_value="/usr/share/zoneinfo/America/Sao_Paulo"):
            self.assertEqual(instance.system_timezone(), "America/Sao_Paulo")
        with mock.patch.dict(os.environ, {"TZ": ""}), mock.patch.object(instance.os.path, "realpath", return_value="/etc/localtime"), \
                mock.patch("builtins.open", side_effect=OSError("none")):
            self.assertEqual(instance.system_timezone(), "UTC", "nothing to go by")
            self.assertIsNone(instance.system_timezone(None), "the install's question says so instead")


if __name__ == "__main__":
    unittest.main()
