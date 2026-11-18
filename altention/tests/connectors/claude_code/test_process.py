"""Is Claude Code running in a pane, from the command lines under it in its terminal's foreground (C-92 b): a Node
dev server is not; Claude installed with npm is; Claude started by a script (the pane then shows the script's shell)
is; a Claude stopped with Ctrl-Z or run in the background is not (#code-review-2 on 6e93ae2)."""
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

from altention.connectors.claude_code import process

NPM = "/srv/lap/.npm-global/lib/node_modules/@anthropic-ai/claude-code/cli.js"


FRONT = 100  # the foreground process group of the terminal, in the tables made here


def procs(*rows):
    """A table() from (pid, parent, words[, process group]) rows, all on one terminal whose foreground group is
    FRONT; a row without a group is in it."""
    return {r[0]: (r[1], r[2], r[3] if len(r) > 3 else FRONT, FRONT) for r in rows}


class Words(unittest.TestCase):
    def test_a_node_dev_server_is_not(self):
        t = procs((10, 1, ["-zsh"]), (11, 10, ["node", "/srv/lap/web/node_modules/.bin/vite", "--port", "5173"]))
        self.assertFalse(process.runs_claude("node", 10, t))
        self.assertFalse(process.runs_claude("node", 11, t))

    def test_claude_from_npm_is(self):
        t = procs((10, 1, ["-zsh"]), (11, 10, ["node", "--no-warnings", NPM, "--model", "opus"]))
        self.assertTrue(process.runs_claude("node", 10, t))

    def test_the_link_npm_installs_is(self):  # #!/usr/bin/env node: the kernel hands node the link's path
        t = procs((10, 1, ["-zsh"]), (11, 10, ["node", "/usr/local/bin/claude"]))
        self.assertTrue(process.runs_claude("node", 10, t))

    def test_claude_started_by_a_script_is(self):
        t = procs((10, 1, ["-zsh"]), (11, 10, ["bash", "/srv/lap/bin/cc-work"]),
                  (12, 11, ["/srv/lap/.local/bin/claude", "--resume"]))
        self.assertTrue(process.runs_claude("bash", 10, t))

    def test_a_shell_alone_is_not(self):
        t = procs((10, 1, ["-zsh"]), (11, 10, ["bash", "/srv/lap/bin/cc-work"]))
        self.assertFalse(process.runs_claude("bash", 10, t))

    def test_a_path_under_dot_claude_is_not(self):
        t = procs((10, 1, ["-zsh"]), (11, 10, ["tail", "-f", "/srv/lap/.claude/projects/x/a.jsonl"]),
                  (12, 10, ["bash", "-c", "source /srv/lap/.claude/shell-snapshots/s.sh"]))
        self.assertFalse(process.runs_claude("tail", 10, t))

    def test_another_pane_s_claude_is_not_counted(self):
        t = procs((10, 1, ["-zsh"]), (20, 1, ["-zsh"]), (21, 20, ["/srv/lap/.local/bin/claude"]))
        self.assertFalse(process.runs_claude("zsh", 10, t))
        self.assertTrue(process.runs_claude("zsh", 20, t))

    def test_the_names_tmux_gives_claude_need_no_table(self):
        self.assertTrue(process.runs_claude("claude", 10, {}))
        self.assertTrue(process.runs_claude("2.1.317", 10, {}))  # macOS: the versioned file claude links to

    def test_the_versioned_file_by_its_path(self):
        t = procs((10, 1, ["/srv/mac/.local/share/claude/versions/2.1.317"]))
        self.assertTrue(process.runs_claude("zsh", 10, t))
        t = procs((10, 1, ["/srv/mac/tools/2.1.317"]))  # a version-shaped name elsewhere is not
        self.assertFalse(process.runs_claude("zsh", 10, t))

    def test_stopped_or_in_the_background_is_not(self):
        """Ctrl-Z, or `claude &`: the shell has the terminal again, and what is typed there reaches it."""
        t = procs((10, 1, ["-bash"], 10), (11, 10, ["/srv/lap/.local/bin/claude"], 11))
        self.assertFalse(process.runs_claude("bash", 10, t))
        t = procs((10, 1, ["-bash"], 10), (11, 10, ["/srv/lap/.local/bin/claude"]))  # brought back with fg
        self.assertTrue(process.runs_claude("bash", 10, t))

    def test_a_script_s_claude_is_in_the_script_s_group(self):
        t = procs((10, 1, ["-bash"], 10), (11, 10, ["bash", "/srv/lap/bin/cc-work"]),
                  (12, 11, ["/srv/lap/.local/bin/claude"]))
        self.assertTrue(process.runs_claude("bash", 10, t))

    def test_not_known_is_none(self):
        self.assertIsNone(process.runs_claude("node", "", {}))
        self.assertIsNone(process.runs_claude("node", 99, {}))  # the pane's process not in the table
        self.assertIsNone(process.runs_claude("node", 10, {}))

    def test_a_listing_goes_by_the_name_when_not_known(self):
        real = process.table
        process.table = lambda: {}
        try:
            lazy = process.Lazy()
            self.assertEqual([lazy("node", 10), lazy("bash", 10)], [True, False])  # the name, as before C-92 b
        finally:
            process.table = real

    def test_lazy_reads_the_table_once(self):
        calls = []
        real = process.table
        process.table = lambda: calls.append(1) or procs((10, 1, ["node", NPM]), (20, 1, ["node", "x.js"]))
        try:
            lazy = process.Lazy()
            self.assertEqual([lazy("node", 10), lazy("node", 20), lazy("claude", 30)], [True, False, True])
        finally:
            process.table = real
        self.assertEqual(calls, [1])


@unittest.skipUnless(shutil.which("tmux"), "no tmux here")
class RealProcesses(unittest.TestCase):
    """The same, as real processes in the panes of a tmux server of the test's own (-S in its folder), read from this
    machine's own table(): `node` and `claude` are links to this Python, so each runs a file that only sleeps; a
    real terminal, so the foreground group is the shell's or the program's as it would be."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="dcq")
        self.sock = os.path.join(self.dir, "s")
        os.makedirs(os.path.join(self.dir, "npm", "claude-code"))
        os.makedirs(os.path.join(self.dir, "bin"))
        for name in ("node", "claude"):
            os.symlink(sys.executable, os.path.join(self.dir, "bin", name))
        self.sleep = os.path.join(self.dir, "sleep.py")
        for f in (self.sleep, os.path.join(self.dir, "npm", "claude-code", "cli.js")):
            with open(f, "w") as out:
                out.write("import time\ntime.sleep(120)\n")
        with open(os.path.join(self.dir, "cc-work"), "w") as out:
            out.write("%s %s\necho done\n" % (os.path.join(self.dir, "bin", "claude"), self.sleep))
        self.tmux("new-session", "-d", "-s", "t", "-x", "80", "-y", "20", "bash --norc --noprofile -i")

    def tearDown(self):
        subprocess.run(["tmux", "-S", self.sock, "kill-server"], stderr=subprocess.DEVNULL, timeout=10)
        shutil.rmtree(self.dir, True)

    def tmux(self, *args):
        return subprocess.run(["tmux", "-S", self.sock] + list(args), stdout=subprocess.PIPE,
                              universal_newlines=True, timeout=10).stdout

    def window(self, name, command):
        self.tmux("new-window", "-t", "t", "-n", name, command)
        return int(self.tmux("display-message", "-p", "-t", "t:" + name, "#{pane_pid}").strip())

    def settled(self, pid, want):
        """runs_claude() of the pane process `pid` (as a pane tmux names bash) once it says `want`, or after 5 s."""
        for _ in range(100):
            if process.runs_claude("bash", pid) == want:
                break
            time.sleep(0.05)
        return process.runs_claude("bash", pid)

    def typed(self, name, line):
        self.tmux("send-keys", "-t", "t:" + name, "-l", line)
        self.tmux("send-keys", "-t", "t:" + name, "Enter")

    def test_dev_server_npm_and_script(self):
        node = os.path.join(self.dir, "bin", "node")
        self.assertFalse(self.settled(self.window("dev", "exec %s %s" % (node, self.sleep)), False))
        self.assertTrue(self.settled(self.window("npm", "exec %s %s/npm/claude-code/cli.js" % (node, self.dir)), True))
        self.assertTrue(self.settled(self.window("wrap", "bash %s/cc-work" % self.dir), True))

    def test_stopped_and_in_the_background_are_not(self):
        claude = "%s %s" % (os.path.join(self.dir, "bin", "claude"), self.sleep)
        pid = self.window("fg", "bash --norc --noprofile -i")
        self.typed("fg", claude)
        self.assertTrue(self.settled(pid, True), "in the foreground of an interactive shell")
        self.tmux("send-keys", "-t", "t:fg", "C-z")
        self.assertFalse(self.settled(pid, False), "stopped with Ctrl-Z: the shell has the terminal")
        self.typed("fg", "fg")
        self.assertTrue(self.settled(pid, True), "brought back with fg")
        pid = self.window("bg", "bash --norc --noprofile -i")
        self.typed("bg", claude + " &")
        for _ in range(100):  # it is there, under the pane, before it is said not to count
            procs = process.table()
            if any(process.claude_argv(procs[p][1]) for p in process.under(pid, procs) if p in procs):
                break
            time.sleep(0.05)
        else:
            self.fail("the background claude never started")
        self.assertFalse(process.runs_claude("bash", pid, procs), "in the background")


if __name__ == "__main__":
    unittest.main()
