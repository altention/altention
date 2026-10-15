"""A window opened on another machine sets its variables with env(1) by its full path: a bare `env` may find something
else first in PATH (uv's installer leaves ~/.local/bin/env, a script for `source`, executable, doing nothing; the
window ended at once, 2026-10-07). With a fake env first in PATH, what the window runs still runs."""
import asyncio
import os
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

from bot import tmuxio
from tests import fakepane


class FullPathEnv(unittest.TestCase):
    def test_the_command_runs_with_a_fake_env_first_in_path(self):
        tmp = tempfile.mkdtemp()
        try:
            fake = os.path.join(tmp, "bin")
            os.makedirs(fake)
            with open(os.path.join(fake, "env"), "w") as f:
                f.write("#!/bin/sh\nexit 0\n")  # what uv leaves: does nothing, says all is well
            os.chmod(os.path.join(fake, "env"), 0o755)
            seen = []

            async def tmux(*args, socket=None, host=None, err=None):
                seen.append(list(args))
                return (1, "") if args[0] == "has-session" else (0, "%1\t123\n")
            out = os.path.join(tmp, "ran")
            with fakepane.replace("tmux", tmux):
                pane = asyncio.run(tmuxio.open_window("/s", "sess", "", "/tmp", ["sh", "-c", 'echo "$FOO" > "%s"' % out],
                                                      env={"FOO": "bar"}, host="far"))
            self.assertEqual(pane["host"], "far")
            argv = seen[-1][seen[-1].index("/tmp") + 1:]  # what tmux runs in the window, after -c <cwd>
            self.assertEqual(argv[0], "/usr/bin/env")
            # run it the way that machine's tmux would, PATH with the fake first
            subprocess.run(argv, env=dict(os.environ, PATH=fake + os.pathsep + os.environ["PATH"]), check=True)
            with open(out) as f:
                self.assertEqual(f.read(), "bar\n", "the command ran, with its variable")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_on_this_machine_tmux_sets_them(self):
        seen = []

        async def tmux(*args, socket=None, host=None, err=None):
            seen.append(list(args))
            return (1, "") if args[0] == "has-session" else (0, "%1\t123\n")
        with fakepane.replace("tmux", tmux):
            asyncio.run(tmuxio.open_window("/s", "sess", "", "/tmp", ["claude"], env={"FOO": "bar"}))
        self.assertIn("-e", seen[-1])
        self.assertNotIn("/usr/bin/env", seen[-1])


if __name__ == "__main__":
    unittest.main()
