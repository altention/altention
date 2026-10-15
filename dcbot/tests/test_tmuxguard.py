"""The stand-in for tmux that keeps every test of dcbot away from real tmux servers (tests/tmuxguard.py)."""
import ast
import os
import subprocess
import sys
import tempfile
import unittest
import unittest.mock

from tests import tmuxguard

HERE = os.path.dirname(os.path.abspath(__file__))


class WhatItRefuses(unittest.TestCase):
    """The stand-in itself, in front of a program that only writes down what reached it: no tmux is run here."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        t = self.tmp.name
        self.realdir = os.path.join(t, "tmux-real")
        os.makedirs(self.realdir)
        os.makedirs(os.path.join(t, "elsewhere"))
        os.symlink(self.realdir, os.path.join(t, "link"))
        self.passed, self.log = os.path.join(t, "passed"), os.path.join(t, "refused")
        real = os.path.join(t, "real-tmux")
        with open(real, "w") as f:
            f.write('#!/bin/sh\necho "$*" >> %s\n' % self.passed)
        os.chmod(real, 0o755)
        self.tmux = tmuxguard.write(t, real, os.path.realpath(self.realdir), self.log)

    def lines(self, path):
        try:
            with open(path) as f:
                return f.read().splitlines()
        except OSError:
            return []

    def run_(self, *args, **env):
        return subprocess.run([self.tmux] + list(args), env=dict(os.environ, DCBOT_TMUXGUARD=self.log, **env), stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, universal_newlines=True, timeout=20)

    def test_what_could_be_somebodys_server_does_not_run(self):
        real, t = self.realdir, self.tmp.name
        for args in (["-L", "dcbot", "list-sessions"], ["-u", "-L", "dcbot", "kill-server"], ["-Ldcbot", "ls"],
                     ["-L", "default", "ls"], ["-L", "work", "new-session", "-d"], ["-L", "dcbot-test-", "ls"],
                     ["-S", real + "/default", "list-panes", "-a"], ["-S", real + "/dcbot", "ls"],
                     ["-u", "-S", t + "/link/default", "ls"], ["-S" + real + "/x", "ls"],
                     ["list-sessions"], ["-u", "kill-server"], ["new-session", "-d", "-s", "x"], []):
            r = self.run_(*args, TMUX=real + "/default,1,0")
            self.assertEqual(r.returncode, 97, args)
            self.assertIn("refused", r.stderr)
        self.assertEqual(self.lines(self.passed), [], "none of them reached the real program")
        self.assertEqual(len(self.lines(self.log)), 14, "each written down")
        self.assertEqual(self.lines(self.log)[0], "tmux -L dcbot list-sessions")

    def test_without_the_place_to_write_it_still_refuses(self):
        env = dict(os.environ)
        env.pop("DCBOT_TMUXGUARD", None)
        r = subprocess.run([self.tmux, "-L", "dcbot", "ls"], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=20)
        self.assertEqual((r.returncode, self.lines(self.passed), self.lines(self.log)), (97, [], ["tmux -L dcbot ls"]))

    def test_one_standing_folder_for_the_same_real_tmux(self):
        with tempfile.TemporaryDirectory() as area, unittest.mock.patch.object(tempfile, "tempdir", area):
            a = tmuxguard.standing("/real/tmux", "/real/dir")
            before = os.stat(os.path.join(a, "tmux")).st_mtime_ns
            self.assertEqual(tmuxguard.standing("/real/tmux", "/real/dir"), a)
            self.assertEqual(os.stat(os.path.join(a, "tmux")).st_mtime_ns, before, "there already: not written again")
            self.assertNotEqual(tmuxguard.standing("/real/tmux", "/another/dir"), a)
            os.chmod(a, 0o777)
            self.assertNotEqual(tmuxguard.standing("/real/tmux", "/real/dir"), a, "others could write there: not used")
            self.assertEqual(os.stat(a).st_mode & 0o777, 0o777, "and left as it was found")

    def test_a_tests_own_server_passes_as_it_was_given(self):
        real, t = self.realdir, self.tmp.name
        ok = (["-L", "dcbot-test-123", "new-session", "-d", "-s", "a b"], ["-u", "-L", "dctest9", "capture-pane", "-S", "-50", "-p"],
              ["-S", t + "/elsewhere/default", "list-panes", "-a"], ["-u", "-S", "/nowhere/at/all/sock", "ls"],
              ["-S", real + "/dcbot-test-7", "ls"], ["-S", real + "/dctest-x", "send-keys", "-l", "-L dcbot"], ["-V"])
        for args in ok:
            self.assertEqual(self.run_(*args).returncode, 0, args)
        self.assertEqual(self.lines(self.passed), [" ".join(a) for a in ok])
        self.assertEqual(self.lines(self.log), [])


class InForce(unittest.TestCase):
    def test_every_test_file_brings_it(self):
        """However the tests are run (the folder, one file, deploy.sh): each file imports it, itself or with the
        fake pane."""
        files = sorted(f for f in os.listdir(HERE) if f.startswith("test_") and f.endswith(".py"))
        self.assertGreater(len(files), 40)
        for f in files:
            with open(os.path.join(HERE, f), encoding="utf-8") as fh:
                tree = ast.parse(fh.read())
            self.assertTrue(any(isinstance(n, ast.ImportFrom) and n.module == "tests"
                                and any(a.name in ("fakepane", "tmuxguard") for a in n.names) for n in tree.body), f)
        with open(os.path.join(HERE, "fakepane.py"), encoding="utf-8") as fh:
            self.assertIn("from tests import tmuxguard", fh.read())

    @unittest.skipUnless(tmuxguard.real_tmux(), "tmux not installed")
    def test_tmux_is_the_stand_in_here_and_in_what_a_test_starts(self):
        import shutil
        mine = shutil.which("tmux")
        self.assertTrue(os.path.basename(os.path.dirname(mine)).startswith("dcbot-tmuxguard-%d-" % os.getuid()), mine)
        with open(mine) as f:
            self.assertIn("tests/tmuxguard.py", f.read())
        self.assertNotEqual(os.path.realpath(mine), os.path.realpath(tmuxguard.real_tmux()))
        r = subprocess.run([sys.executable, "-c", "import shutil; print(shutil.which('tmux'))"], stdout=subprocess.PIPE, universal_newlines=True)
        self.assertEqual(r.stdout.strip(), mine)

    @unittest.skipUnless(tmuxguard.real_tmux(), "tmux not installed")
    def test_a_test_that_reaches_for_a_real_server_fails(self):
        """Also when the code that started tmux did not look at how it ended. Run as a test file of its own, in a
        process of its own (with its own stand-in); the server it names is not one anybody has."""
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "tests"))
            for f in ("tmuxguard.py",):
                with open(os.path.join(HERE, f)) as src, open(os.path.join(tmp, "tests", f), "w") as dst:
                    dst.write(src.read())
            with open(os.path.join(tmp, "tests", "test_inner.py"), "w") as f:
                f.write("import subprocess, unittest\nfrom tests import tmuxguard  # noqa: F401\n\n\n"
                        "class T(unittest.TestCase):\n"
                        "    def test_reaches(self):\n"
                        "        subprocess.run(['tmux', '-L', 'nobodys-server-17', 'has-session', '-t', '=none'], stderr=subprocess.DEVNULL)\n\n"
                        "    def test_its_own(self):\n"
                        "        subprocess.run(['tmux', '-L', 'dcbot-test-none', 'has-session', '-t', '=none'], stderr=subprocess.DEVNULL)\n\n"
                        "    def test_nothing(self):\n"
                        "        import shutil\n"
                        "        with open('standin', 'w') as f:\n"
                        "            f.write(shutil.which('tmux'))\n")
            env = dict(os.environ)
            env.pop("DCBOT_TMUXGUARD")
            env["PATH"] = os.pathsep.join(p for p in env["PATH"].split(os.pathsep) if "dcbot-tmuxguard-" not in p)
            for how in (["-m", "unittest", "discover", "-s", "tests"], ["-m", "unittest", "tests.test_inner"]):
                r = subprocess.run([sys.executable] + how, cwd=tmp, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   universal_newlines=True, timeout=60)
                self.assertNotEqual(r.returncode, 0, r.stdout)
                self.assertIn("FAILED (failures=1)", r.stdout)
                self.assertIn("FAIL: test_reaches", r.stdout)
                self.assertIn("tmux -L nobodys-server-17 has-session", r.stdout)
                self.assertIn("Ran 3 tests", r.stdout)
                with open(os.path.join(tmp, "standin")) as f:
                    standin = f.read()
                self.assertTrue(os.path.basename(os.path.dirname(standin)).startswith("dcbot-tmuxguard-"), standin)
                late = subprocess.run([standin, "-L", "nobodys-server-17", "has-session", "-t", "=none"], env=env,
                                      stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, timeout=20)
                self.assertEqual(late.returncode, 97, "that run has ended; what it left behind still meets the stand-in")


if __name__ == "__main__":
    unittest.main()
