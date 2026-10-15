"""Words sent to a shell on another machine arrive as they were (2026-10-07, #handover-0): shlex.quote leaves
"=name" bare, and zsh, the login shell of some machines, reads a bare word starting with = as the path of a
command, so every remote tmux command naming a session by "=name" failed with "zsh:1: name not found" and the
window was never opened. Checked through a real `zsh -c` (skipped where there is no zsh), and the remote path of
open_window end to end: a fake ssh running the command with zsh, a real tmux server."""
import asyncio
import contextlib
import importlib.machinery
import importlib.util
import io
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from bot import remote, tmuxio

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENGINE = os.path.dirname(ROOT)
if ENGINE not in sys.path:
    sys.path.insert(0, ENGINE)
from altention import config  # noqa: E402
from tests import tmuxguard  # noqa: F401,E402  (no test reaches a real tmux server)

WORDS = ["=my-project", "=my-project:", "=name", "~x", "~", "a*", "a?b", "[ab]", "*", "plain", "two words",
         "it's", "$HOME", "`id`", "!!", "a=b", "#{pane_id}\t#{pane_pid}", "%34", "-t", "", "雙引號\"與'單引號"]
ZSH = shutil.which("zsh")


def bin_tool(name):
    loader = importlib.machinery.SourceFileLoader("bin_" + name, os.path.join(ENGINE, "altention", "dc", name + ".py"))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def quoters():
    return {"remote.quote": remote.quote, "config.shell_quote": config.shell_quote,
            **{"altention dc %s quote" % n: bin_tool(n).quote for n in ("post", "notify", "read")}}


@unittest.skipUnless(ZSH, "no zsh on this machine: the words are checked with sh only (zsh is what broke them)")
class ThroughZsh(unittest.TestCase):
    def argv_after(self, shell, quote):
        cmd = "printf '%s\\0' " + " ".join(quote(w) for w in WORDS)
        out = subprocess.run([shell, "-f", "-c", cmd] if shell == ZSH else [shell, "-c", cmd], stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, timeout=10)
        return out.returncode, out.stdout.decode("utf-8").split("\0")[:-1], out.stderr.decode("utf-8")

    def test_every_word_arrives_as_it_was(self):
        for name, quote in quoters().items():
            for shell in (ZSH, "/bin/sh"):
                with self.subTest(quote=name, shell=shell):
                    code, got, err = self.argv_after(shell, quote)
                    self.assertEqual((code, got), (0, WORDS), err)

    def test_what_went_wrong_before(self):
        self.assertEqual(shlex.quote("=my-project"), "=my-project", "left bare")
        r = subprocess.run([ZSH, "-f", "-c", "printf '%s\\n' " + shlex.quote("=my-project")],
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, timeout=10)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("my-project not found", r.stderr.replace(": not found", " not found"))

    def test_the_engine_path_keeps_its_tilde_for_the_shell_there(self):
        home = subprocess.run([ZSH, "-f", "-c", "printf %s " + config.shell_path("~/=my engine")], stdout=subprocess.PIPE,
                              universal_newlines=True, timeout=10).stdout
        self.assertEqual(home, os.path.expanduser("~") + "/=my engine")


@unittest.skipUnless(ZSH and shutil.which("tmux"), "needs zsh and tmux")
class RemoteWindow(unittest.TestCase):
    """open_window on machine "far": its ssh is a fake one running the command with zsh, as that machine would."""
    FAKE = """#!/bin/sh
for a in "$@"; do cmd="$a"; done
exec %s -f -c "$cmd"
"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="dcq")
        b = os.path.join(self.tmp, "bin")
        os.makedirs(b)
        with open(os.path.join(b, "ssh"), "w") as f:
            f.write(self.FAKE % ZSH)
        os.chmod(os.path.join(b, "ssh"), 0o755)
        self.socket = os.path.join(self.tmp, "s")
        self.patches = [mock.patch.dict(os.environ, {"PATH": b + os.pathsep + os.environ["PATH"]}),
                        mock.patch.object(remote, "inventory", return_value={"machines": {"far": {"ssh": ["FAR"]}}})]
        for p in self.patches:
            p.start()
        remote.LAST_GOOD.clear()
        remote.TRIED.clear()
        self.problem, remote.problem = remote.problem, None

    def tearDown(self):
        remote.problem = self.problem
        for p in reversed(self.patches):
            p.stop()
        remote.LAST_GOOD.clear()
        subprocess.run(["tmux", "-S", self.socket, "kill-server"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def windows(self, session):
        return subprocess.run(["tmux", "-S", self.socket, "list-windows", "-t", session, "-F", "#{window_name}"],
                              stdout=subprocess.PIPE, universal_newlines=True).stdout.split()

    def test_a_new_window_in_the_session_that_is_there(self):
        subprocess.run(["tmux", "-S", self.socket, "new-session", "-d", "-s", "my-project", "cat"], check=True)
        why = []
        pane = asyncio.run(tmuxio.open_window(self.socket, "my-project", "dc", "/tmp", ["cat"], {"X": "1"},
                                              host="far", why=why))
        self.assertTrue(pane and pane["id"].startswith("%"), why)
        self.assertEqual(pane["host"], "far")
        self.assertEqual(len(self.windows("my-project")), 2, "a window in it, not a second session")
        self.assertIn("dc", self.windows("my-project"))
        self.assertEqual(why, [])

    def test_a_session_not_there_is_made(self):
        pane = asyncio.run(tmuxio.open_window(self.socket, "new-one", "", "/tmp", ["cat"], host="far"))
        self.assertTrue(pane)
        self.assertEqual(len(self.windows("new-one")), 1)

    def test_when_it_fails_what_the_shell_said_is_told(self):
        """The old quoting brought back: the window is not opened and the reason is no longer lost."""
        subprocess.run(["tmux", "-S", self.socket, "new-session", "-d", "-s", "my-project", "cat"], check=True)
        why, out = [], io.StringIO()
        with mock.patch.object(remote, "quote", shlex.quote), contextlib.redirect_stdout(out):
            pane = asyncio.run(tmuxio.open_window(self.socket, "my-project", "", "/tmp", ["cat"], host="far", why=why))
        self.assertIsNone(pane)
        self.assertEqual(len(why), 1)
        self.assertIn("my-project not found", why[0])
        self.assertIn("duplicate session", why[0], "has-session failed, so new-session was tried")
        self.assertIn("open_window: far 上的 my-project: zsh", out.getvalue(), "for supervisor.out")

    def test_a_machine_not_reached_sends_nothing_more(self):
        sent = []
        real = remote._once

        async def once(host, alias, command, input, timeout):
            sent.append(command)
            return (255, "", "ssh: connect to host FAR port 22: Connection refused") if command.endswith("true") \
                else await real(host, alias, command, input, timeout)
        why = []
        with mock.patch.object(remote, "_once", once), contextlib.redirect_stdout(io.StringIO()):
            self.assertIsNone(asyncio.run(tmuxio.open_window(self.socket, "x", "", "/tmp", ["cat"], host="far", why=why)))
        self.assertEqual(len(sent), 1, "only the probe")
        self.assertEqual(why, ["連不上 far（試過 FAR）"])


@unittest.skipUnless(shutil.which("tmux"), "needs tmux")
class LocalWhy(unittest.TestCase):
    def test_here_too_tmux_says_why(self):
        tmp = tempfile.mkdtemp(prefix="dcq")
        try:
            err = []
            code, _ = asyncio.run(tmuxio.tmux("has-session", "-t", "=nope", socket=os.path.join(tmp, "s"), err=err))
            self.assertNotEqual(code, 0)
            self.assertTrue(err and ("no server" in err[0] or "error connecting" in err[0]), err)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
