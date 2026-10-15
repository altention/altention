"""ops/instance.py: seed an empty instance repo from an engine, join it from two "machines" (two HOMEs)."""
import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))))
from altention import testing as _testing  # noqa: E402
_testing.guard_home()  # tests never touch the real home (altention/testing.py)
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
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
            env = dict(os.environ, HOME=home, CHATLOG="0")
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
