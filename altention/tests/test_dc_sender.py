"""altention/dc/sender.py (C-186 P0-2): the Claude a command runs under, by its own environment."""
import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))))
from altention import testing as _testing  # noqa: E402
_testing.guard_home()  # tests never touch the real home (altention/testing.py)
import os
import subprocess
import tempfile
import unittest

from altention.dc import sender


class Proc(object):
    """A made-up /proc: {pid: (ppid, [command line], {env})}."""
    def __init__(self, tree):
        self.dir = tempfile.TemporaryDirectory()
        os.makedirs(os.path.join(self.dir.name, "1"))
        for pid, (ppid, argv, env) in tree.items():
            d = os.path.join(self.dir.name, str(pid))
            os.makedirs(d, exist_ok=True)
            with open(os.path.join(d, "stat"), "w") as f:
                f.write("%d (%s) S %d 1 1 0 -1\n" % (pid, os.path.basename(argv[0])[:15], ppid))
            with open(os.path.join(d, "cmdline"), "wb") as f:
                f.write(b"\0".join(w.encode() for w in argv) + b"\0")
            if env is not None:
                with open(os.path.join(d, "environ"), "wb") as f:
                    f.write(b"\0".join(("%s=%s" % kv).encode() for kv in env.items()) + b"\0")


class Linux(unittest.TestCase):
    def find(self, tree, start):
        proc = Proc(tree)
        self.addCleanup(proc.dir.cleanup)
        return sender.claude_env(start, proc.dir.name)

    def test_the_nearest_claudes_own_environment(self):
        tree = {50: (40, ["python3", "post.py"], {"DCBOT_CHANNEL": "999"}),  # the command's own: the caller's to set
                40: (30, ["bash", "-c", "claude"], {"DCBOT_CHANNEL": "999"}),  # (a word "claude" is not Claude Code)
                30: (20, ["claude", "--model", "x"], {"DCBOT_CHANNEL": "555", "HOME": "/h", "TMUX_PANE": "%3",
                                                      "TMUX": "/s,1,0"}),
                20: (1, ["claude"], {"DCBOT_CHANNEL": "111"})}
        self.assertEqual(self.find(tree, 50), {"DCBOT_CHANNEL": "555", "TMUX_PANE": "%3", "TMUX": "/s,1,0"})

    def test_a_name_with_spaces_and_brackets(self):
        tree = {50: (45, ["tmux: server"], None), 45: (30, ["x) (y"], None), 30: (1, ["claude"], {"TMUX_PANE": "%9"})}
        self.assertEqual(self.find(tree, 50), {"TMUX_PANE": "%9"})

    def test_claude_installed_with_npm(self):
        tree = {50: (40, ["sh"], None), 40: (30, ["node", "/opt/x/node_modules/vite/bin/vite.js"], {"DCBOT_CHANNEL": "9"}),
                30: (1, ["node", "/usr/lib/node_modules/@anthropic-ai/claude-code/cli.js"], {"DCBOT_CHANNEL": "7"})}
        self.assertEqual(self.find(tree, 50), {"DCBOT_CHANNEL": "7"})

    def test_no_claude_above_or_unreadable(self):
        self.assertIsNone(self.find({50: (40, ["python3"], {}), 40: (1, ["cron"], {})}, 50))
        self.assertIsNone(self.find({50: (40, ["python3"], {})}, 50), "a parent gone")
        self.assertIsNone(self.find({50: (30, ["bash"], {}), 30: (1, ["claude"], None)}, 50), "its environment not readable")
        self.assertEqual(self.find({50: (30, ["bash"], {}), 30: (1, ["claude"], {"PATH": "/bin"})}, 50), {})


class Mac(unittest.TestCase):
    def test_through_ps(self):
        tree = {50: (40, "/usr/bin/python3 post.py", ""), 40: (30, "-zsh", ""),
                30: (1, "/Users/x/.local/share/claude/versions/2.1.330 --model opus",
                     "claude --model opus DCBOT_CHANNEL=555 TMUX_PANE=%3 PATH=/bin")}
        calls = []

        def run(cmd, **kw):
            calls.append(cmd)
            pid = int(cmd[-1])
            if cmd[1] == "eww":
                return subprocess.CompletedProcess(cmd, 0, tree[pid][2] + "\n", "")
            if pid not in tree:
                return subprocess.CompletedProcess(cmd, 1, "", "")
            return subprocess.CompletedProcess(cmd, 0, "  %d %s\n" % (tree[pid][0], tree[pid][1]), "")
        with tempfile.TemporaryDirectory() as noproc:
            self.assertEqual(sender.claude_env(50, noproc, run), {"DCBOT_CHANNEL": "555", "TMUX_PANE": "%3"})
            tree[30] = (1, "/bin/launchd", "")
            self.assertIsNone(sender.claude_env(50, noproc, run))


if __name__ == "__main__":
    unittest.main()
