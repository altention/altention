"""bin/altention: one entry point that hands over to the tools doing the work."""
import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))))
from altention import testing as _testing  # noqa: E402
_testing.guard_home()  # tests never touch the real home (altention/testing.py)
import importlib.machinery
import importlib.util
import os
import subprocess
import sys
import unittest

ENGINE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PATH = os.path.join(ENGINE, "bin", "altention")
loader = importlib.machinery.SourceFileLoader("altention_cli", PATH)
cli = importlib.util.module_from_spec(importlib.util.spec_from_loader("altention_cli", loader))
loader.exec_module(cli)


class Target(unittest.TestCase):
    def test_each_command_goes_to_its_tool(self):
        for argv, tool, rest in ((["doctor", "host-a", "--json"], "ops/doctor.py", ["host-a", "--json"]),
                                 (["apply", "all", "--dry-run"], "ops/apply.py", ["all", "--dry-run"]),
                                 (["plugins", "show"], "ops/plugins.py", ["show"]),
                                 (["dc", "post", "#project-a", "中文 訊息"], "altention/dc/post.py", ["#project-a", "中文 訊息"]),
                                 (["dc", "read", "#project-a", "--threads"], "altention/dc/read.py", ["#project-a", "--threads"]),
                                 (["dc", "notify", "hi"], "altention/dc/notify.py", ["hi"])):
            path, args = cli.target(argv)
            self.assertEqual((os.path.relpath(path, ENGINE), args), (tool, rest), argv)
            self.assertTrue(os.path.exists(path), tool)

    def test_help_and_unknown(self):
        for argv, code in (([], 0), (["--help"], 0), (["bogus"], 2), (["dc"], 2), (["dc", "bogus"], 2)):
            r = subprocess.run([sys.executable, PATH] + argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               universal_newlines=True)
            self.assertEqual(r.returncode, code, argv)
            self.assertIn("altention", r.stdout)

    def test_it_hands_over_with_the_arguments(self):
        r = subprocess.run([sys.executable, PATH, "plugins", "nothing-such"], stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE, universal_newlines=True)
        self.assertEqual(r.returncode, 2, "plugins.py's own usage exit status")
        self.assertIn("alt plugins", r.stdout)


class Link(unittest.TestCase):
    """altention --link (install.sh): ~/.local/bin/altention and alt, never taking what belongs to another program."""

    def setUp(self):
        import tempfile
        self.tmp = tempfile.mkdtemp(prefix="連結 bin ")
        self.bindir = os.path.join(self.tmp, ".local", "bin")
        self.src = os.path.realpath(PATH)
        self.said = []

    def tearDown(self):
        import shutil
        for root, dirs, _ in os.walk(self.tmp):
            for d in dirs:
                os.chmod(os.path.join(root, d), 0o755)
        os.chmod(self.tmp, 0o755)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def link(self, which=lambda name: None):
        return cli.link(self.bindir, which, self.said.append)

    def at(self, name):
        return os.path.realpath(os.path.join(self.bindir, name))

    def test_fresh_and_again(self):
        self.assertTrue(self.link())
        self.assertTrue(self.link())
        self.assertEqual((self.at("altention"), self.at("alt")), (self.src, self.src))

    def test_another_programs_alt_link_is_left_alone(self):
        """pipx and npm put their links in ~/.local/bin: which() finds that very link."""
        other = os.path.join(self.tmp, "venvs", "x", "bin", "alt")
        os.makedirs(os.path.dirname(other))
        open(other, "w").close()
        os.makedirs(self.bindir)
        os.symlink(other, os.path.join(self.bindir, "alt"))
        self.link(which=lambda name: os.path.join(self.bindir, name))
        self.assertEqual(self.at("alt"), os.path.realpath(other))
        self.assertTrue(any("別的程式的連結" in t for t in self.said))

    def test_a_link_to_an_old_engines_altention_or_to_nothing_is_updated(self):
        old = os.path.join(self.tmp, "old engine", "bin", "altention")
        os.makedirs(os.path.dirname(old))
        open(old, "w").close()
        os.makedirs(os.path.join(self.tmp, "old engine", "altention"))
        open(os.path.join(self.tmp, "old engine", "altention", "config.py"), "w").close()  # an engine's
        os.makedirs(self.bindir)
        os.symlink(old, os.path.join(self.bindir, "altention"))
        os.symlink(os.path.join(self.tmp, "gone"), os.path.join(self.bindir, "alt"))
        self.assertTrue(self.link())
        self.assertEqual((self.at("altention"), self.at("alt")), (self.src, self.src))

    def test_a_link_through_an_old_name_of_this_engine_is_rewritten(self):
        """C-17 S4: the engine moved and its old folder is a link to it; ~/.local/bin/altention still names the old
        folder. It resolves to this very file today, and breaks once the old name goes: it is written anew."""
        old_name = os.path.join(self.tmp, "old name")
        os.symlink(os.path.dirname(os.path.dirname(self.src)), old_name)
        via = os.path.join(old_name, "bin", "altention")
        os.makedirs(self.bindir)
        os.symlink(via, os.path.join(self.bindir, "altention"))
        self.assertEqual(self.at("altention"), self.src, "the same file today")
        self.assertTrue(self.link())
        self.assertEqual(os.readlink(os.path.join(self.bindir, "altention")), self.src)
        self.said.clear()
        self.link()
        self.assertTrue(all("✓" in t for t in self.said), "then nothing to change")

    def test_someones_own_program_called_altention_is_left(self):
        """#code-review-2 2026-10-16: the name alone made it another engine's and it was rewritten."""
        theirs = os.path.join(self.tmp, "tools", "altention")
        os.makedirs(os.path.dirname(theirs))
        with open(theirs, "w") as f:
            f.write("#!/bin/sh\necho mine\n")
        os.makedirs(self.bindir)
        os.symlink(theirs, os.path.join(self.bindir, "altention"))
        self.assertFalse(self.link())
        self.assertEqual(os.readlink(os.path.join(self.bindir, "altention")), theirs)
        self.assertTrue(any("別的程式的連結" in t for t in self.said))

    def test_a_plain_file_is_left_alone(self):
        os.makedirs(self.bindir)
        with open(os.path.join(self.bindir, "altention"), "w") as f:
            f.write("mine\n")
        self.assertFalse(self.link())
        with open(os.path.join(self.bindir, "altention")) as f:
            self.assertEqual(f.read(), "mine\n")

    def test_another_alt_on_path_keeps_its_name(self):
        self.link(which=lambda name: "/opt/x/bin/alt" if name == "alt" else None)
        self.assertFalse(os.path.lexists(os.path.join(self.bindir, "alt")))
        self.assertEqual(self.at("altention"), self.src)

    def test_nothing_writable_is_said_not_raised(self):
        os.makedirs(os.path.join(self.tmp, ".local"))
        with open(self.bindir, "w") as f:  # ~/.local/bin is a file
            f.write("x")
        self.assertFalse(self.link())
        self.assertTrue(any("建不起來" in t for t in self.said))
        os.remove(self.bindir)
        os.makedirs(self.bindir)
        os.chmod(self.bindir, 0o555)
        if not os.access(self.bindir, os.W_OK):  # (root can write anyway)
            self.assertFalse(self.link())

    def test_install_sh_goes_on_when_linking_fails(self):
        """The installer (ops/install.py since 3a.9) says skip for the command and goes on, never fails on it."""
        with open(os.path.join(ENGINE, "ops", "install.py"), encoding="utf-8") as f:
            text = f.read()
        body = text[text.index("def entry(r):"):text.index("def links(")]
        self.assertIn('r.say("skip", "core.entry"', body)
        self.assertNotIn('"fail"', body)


if __name__ == "__main__":
    unittest.main()
