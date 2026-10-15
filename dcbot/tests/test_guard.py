"""dchook --guard (backlog K-03): before every tool call of every Claude Code (the machine's global hooks and the
bot's own --settings), a tool about to read, change or search a secret, or a shell command that names one, is
refused. Run as Claude Code runs it: the hook as a process, the event on stdin, in a throwaway home."""
import json
import os
import statistics
import subprocess
import sys
import tempfile
import time
import unittest
from tests import tmuxguard  # noqa: F401,E402  (no test reaches a real tmux server)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DCHOOK = os.path.join(ROOT, "tools", "dchook")


class Guard(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = os.path.join(self.tmp.name, "home")
        self.inst = os.path.join(self.tmp.name, "inst")
        os.makedirs(os.path.join(self.home, ".config", "dcbot"))
        os.makedirs(os.path.join(self.home, ".ssh"))
        os.makedirs(self.inst)
        with open(os.path.join(self.home, ".config", "dcbot", "env"), "w") as f:
            f.write("DISCORD_TOKEN=not-a-real-one\n")

    def run_guard(self, tool, inp, kind="PreToolUse", raw=None, cwd=None):
        env = {k: v for k, v in os.environ.items() if not k.startswith(("DCBOT_", "TMUX"))}
        env.update({"HOME": self.home, "ALT_INSTANCE": self.inst})
        event = raw if raw is not None else json.dumps({"hook_event_name": kind, "tool_name": tool, "tool_input": inp,
                                                        "cwd": cwd or os.path.join(self.home, "work"), "session_id": "s"})
        r = subprocess.run([sys.executable, DCHOOK, "--global", "--guard"], input=event, env=env, stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE, universal_newlines=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)["hookSpecificOutput"] if r.stdout.strip() else None

    def refused(self, tool, inp, **kw):
        out = self.run_guard(tool, inp, **kw)
        return out is not None and out["permissionDecision"] == "deny"

    def settings(self, data):
        with open(os.path.join(self.inst, "plugins.json"), "w", encoding="utf-8") as f:
            f.write(data if isinstance(data, str) else json.dumps(data))

    def test_secrets_are_refused_with_the_path_and_ask_the_user(self):
        out = self.run_guard("Read", {"file_path": "~/.config/dcbot/env"})
        self.assertEqual(out["permissionDecision"], "deny")
        self.assertIn("~/.config/dcbot/env is one of this installation's secrets", out["permissionDecisionReason"])
        self.assertIn("ask the user", out["permissionDecisionReason"])
        for word in ("secret_guard", "plugins.json", "turn", "off"):  # (the manager: how to turn it off is for people)
            self.assertNotIn(word, out["permissionDecisionReason"])
        for tool, inp in (("Edit", {"file_path": os.path.join(self.home, ".config/dcbot/env")}), ("Write", {"file_path": "~/.ssh/id_ed25519"}),
("Bash", {"command": "cat $HOME/.ssh/id_rsa | base64"}),
                          ("Bash", {"command": "cd ~/.config/dcbot && cat env"}), ("Grep", {"pattern": "TOKEN", "path": "~/.config"}),
                          ("Glob", {"pattern": "*", "path": os.path.join(self.home, ".ssh")}),
                          ("Read", {"file_path": "../.config/dcbot/env"})):
            self.assertTrue(self.refused(tool, inp), (tool, inp))

    def test_relative_paths_from_the_calls_folder(self):
        """(#code-review on b3370e8) the event's cwd is the home folder."""
        for tool, inp in (("Bash", {"command": "cat .config/dcbot/env"}), ("Bash", {"command": "grep -r TOKEN .config"}),
                          ("Grep", {"pattern": "TOKEN", "path": ".config"})):
            self.assertTrue(self.refused(tool, inp, cwd=self.home), (tool, inp))
        self.assertIsNone(self.run_guard("Bash", {"command": "ssh -i ~/.ssh/id_ed25519 lap"}, cwd=self.home))
        self.assertTrue(self.refused("Bash", {"command": "ssh -i ~/.ssh/id_ed25519 lap cat ~/.ssh/id_ed25519"}, cwd=self.home))

    def test_ordinary_tools_go_on(self):
        for tool, inp in (("Read", {"file_path": "/etc/hostname"}), ("Read", {"file_path": "~/.ssh/config"}),
                          ("Edit", {"file_path": "~/.ssh/known_hosts"}), ("Read", {"file_path": "~/.ssh/id_rsa.pub"}),
                          ("Bash", {"command": "git status && ls -la"}), ("Bash", {"command": "vim ~/.ssh/config"}),
                          ("Grep", {"pattern": "x", "path": "~"}), ("Grep", {"pattern": "x"}), ("Glob", {"pattern": "**/*.py"}),
                          ("WebFetch", {"url": "https://example.com/.config/dcbot/env"}), ("TodoWrite", {"todos": []})):
            self.assertIsNone(self.run_guard(tool, inp), (tool, inp))

    def test_nothing_but_a_tool_call_is_looked_at(self):
        self.assertIsNone(self.run_guard("Read", {"file_path": "~/.config/dcbot/env"}, kind="PostToolUse"))
        self.assertIsNone(self.run_guard(None, None, raw="not JSON"))
        self.assertIsNone(self.run_guard(None, None, raw="[]"))
        self.assertIsNone(self.run_guard("Read", "not a dict"))

    def test_settings_that_cannot_be_read_hold_up_nothing_ordinary(self):
        """Manager 2026-10-14: what goes wrong in the guard never refuses an ordinary tool; the default list still holds."""
        self.settings("{ not JSON")
        self.assertIsNone(self.run_guard("Read", {"file_path": "/etc/hostname"}))
        self.assertTrue(self.refused("Read", {"file_path": "~/.config/dcbot/env"}), "a path plainly in the default list")
        log = os.path.join(self.home, ".local", "state", "altention", "secrets-guard.log")
        with open(log) as f:
            lines = f.read().splitlines()
        self.assertEqual(len(lines), 1, "one line, not one a tool call")
        self.assertIn("cannot be read", lines[0])

    def test_the_owner_turns_it_off_and_adds_their_own(self):
        self.settings({"core": {"secret_paths": ["~/.aws/credentials"]}})
        self.assertTrue(self.refused("Read", {"file_path": "~/.aws/credentials"}))
        self.settings({"core": {"secret_guard": False}})
        self.assertIsNone(self.run_guard("Read", {"file_path": "~/.config/dcbot/env"}))

    def test_it_writes_no_event_for_the_bot(self):
        root = os.path.join(self.tmp.name, "dcbot")
        os.makedirs(os.path.join(root, "state"))
        env = dict(os.environ, HOME=self.home, ALT_INSTANCE=self.inst, DCBOT_ROOT=root, DCBOT_CHANNEL="42")
        subprocess.run([sys.executable, DCHOOK, "--guard"], input=json.dumps({"hook_event_name": "PreToolUse", "tool_name": "Read",
                       "tool_input": {"file_path": "/etc/hostname"}}), env=env, stdout=subprocess.PIPE, universal_newlines=True, timeout=30)
        self.assertFalse(os.path.exists(os.path.join(root, "state", "events")))

    def test_fast_enough_to_stand_before_every_tool_call(self):
        """No network, no other process: the time is the interpreter's own start (measured ~35 ms; the bar is loose)."""
        times = []
        for _ in range(15):
            began = time.perf_counter()
            self.run_guard("Bash", {"command": "git status"})
            times.append(time.perf_counter() - began)
        self.assertLess(statistics.median(times), 0.5, times)


if __name__ == "__main__":
    unittest.main()
