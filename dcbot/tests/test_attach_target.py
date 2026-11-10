"""C-154: `altention dc post --attach <tmux target>` refuses a target tmux has no pane for. tmux 3.4's display-message
answers for the current pane, exit 0, when the window or pane a target names is not there (a:7, a:0.5), and for a
session that is not there it answered with an empty session and %0 (tried 2026-11-03): the post would have attached
another pane. The place tmux answered for is compared with the target (post.same_place)."""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from tests import tmuxguard  # noqa: F401,E402  (no test reaches a real tmux server)
from altention.dc import post  # noqa: E402

CURRENT = ("a", "1", "zsh", "@1", "0", "%1")  # session a, window 1 (named zsh, @1), pane 0: %1


class SamePlace(unittest.TestCase):
    def test_what_names_the_pane_tmux_answered_for(self):
        for target in ("a:1", "a:1.0", "%1", "a", "a:zsh", "a:z", "a:@1", "=a:1", "a:!", "a:1.{top}", ":1",
                       "zsh", "zs", "1", "@1"):  # one name: a session, else a window (#code-review on 23f533d)
            self.assertTrue(post.same_place(target, *CURRENT), target)

    def test_what_names_another(self):
        for target in ("a:7", "a:0.5", "a:1.3", "%9", "b", "ab:1", "=ab:1", "a:vim", "a:@2", "a:=z", "vim", "7",
                       "@2"):
            self.assertFalse(post.same_place(target, *CURRENT), target)

    def test_a_session_tmux_did_not_find(self):
        self.assertFalse(post.same_place("b", "", "0", "", "", "0", "%0"), "tmux 3.4's answer for a session not there")


@unittest.skipUnless(shutil.which("tmux"), "no tmux here")
class RealTmux(unittest.TestCase):
    """pane_address() against a tmux server of the test's own (-S in its own folder): two windows."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="dcq")
        self.sock = os.path.join(self.dir, "s")
        for args in (["new-session", "-d", "-s", "work", "-x", "80", "-y", "20", "-n", "main"],
                     ["new-window", "-t", "work:", "-n", "editor"]):
            subprocess.run(["tmux", "-S", self.sock] + args, check=True, timeout=10)
        self.ids = subprocess.run(["tmux", "-S", self.sock, "list-panes", "-a", "-F", "#{window_index} #{pane_id}"],
                                  stdout=subprocess.PIPE, universal_newlines=True, timeout=10).stdout.split("\n")

    def tearDown(self):
        subprocess.run(["tmux", "-S", self.sock, "kill-server"], stderr=subprocess.DEVNULL, timeout=10)
        shutil.rmtree(self.dir, True)

    def pane_of(self, window):
        return next(row.split()[1] for row in self.ids if row.startswith("%s " % window))

    def test_a_pane_that_is_there(self):
        for target, window in (("work:0", 0), ("work:1", 1), ("work:0.0", 0), ("wo:1", 1), ("work:editor", 1),
                               ("editor", 1), ("ed", 1), ("main", 0)):  # a window's name alone too
            self.assertEqual(post.pane_address(target, self.sock)["pane"], self.pane_of(window), target)
        self.assertEqual(post.pane_address(self.pane_of(0), self.sock)["pane"], self.pane_of(0))

    def test_one_that_is_not_is_refused(self):
        for target in ("work:7", "work:0.5", "nope", "nope:0", "%99", "work:vim"):
            with self.assertRaises(SystemExit) as e:
                post.pane_address(target, self.sock)
            self.assertIn(target, str(e.exception), target)


class Hangs(unittest.TestCase):
    def test_a_tmux_that_does_not_answer_is_one_line(self):
        from unittest import mock

        def hang(*a, **kw):
            raise subprocess.TimeoutExpired(a[0], 10)
        with mock.patch.object(post.subprocess, "run", hang), self.assertRaises(SystemExit) as e:
            post.pane_address("work:0")
        self.assertEqual(str(e.exception), "altention dc post：找不到 tmux 的 work:0（tmux 10 秒沒有回答）")


if __name__ == "__main__":
    unittest.main()
