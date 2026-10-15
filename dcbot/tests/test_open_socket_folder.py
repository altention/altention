"""A server whose socket folder is gone (its machine restarted, and nothing has started tmux there by default since):
open_window makes the folder before new-session, which makes the socket but not its folder (manager 2026-10-14)."""
import asyncio
import os
import shutil
import stat
import subprocess
import tempfile
import unittest
from unittest import mock

import bot  # noqa: F401  (first: it puts the engine's folder on the path; the connectors lie beside dcbot/)
from bot import remote, tmuxio
from tests import fakepane
from tests import tmuxguard  # noqa: F401,E402  (no test reaches a real tmux server)

GONE = "error connecting to %s (No such file or directory)"


class SocketFolder(unittest.TestCase):
    def open(self, socket, host=None, says=None):
        seen, ran = [], []

        async def tmux(*args, socket=None, host=None, err=None):
            seen.append(args[0])
            if args[0] == "has-session":
                if says and err is not None:
                    err.append(says)
                return 1, ""
            return 0, "%1\t123\n"

        async def run(h, command, input=None, timeout=30, err=None):
            ran.append((h, command))
            return 0, ""
        with fakepane.replace("tmux", tmux), mock.patch.object(remote, "run", run):
            pane = asyncio.run(tmuxio.open_window(socket, "sess", "", "/tmp", ["claude"], host=host))
        return pane, seen, ran

    def test_here_the_folder_is_made_owner_only(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        socket = os.path.join(tmp, "tmux-7", "default")
        pane, seen, ran = self.open(socket, says=GONE % socket)
        self.assertEqual((pane["id"], seen, ran), ("%1", ["has-session", "new-session"], []))
        self.assertEqual(stat.S_IMODE(os.stat(os.path.dirname(socket)).st_mode), 0o700)

    def test_on_another_machine_it_is_made_there(self):
        pane, seen, ran = self.open("/tmp/tmux-7/default", host="lap", says=GONE % "/tmp/tmux-7/default")
        self.assertEqual((pane["host"], seen[-1]), ("lap", "new-session"))
        self.assertEqual(ran, [("lap", "mkdir -p -m 700 '/tmp/tmux-7'")])

    def test_only_when_tmux_says_the_socket_is_not_there(self):
        for says in (None, "no server running on /tmp/tmux-7/default", "can't find session: sess"):
            self.assertEqual(self.open("/tmp/tmux-7/default", host="lap", says=says)[2], [], says)

    def test_real_tmux_needs_it(self):
        """What the fix rests on, with the real tmux: new-session at a socket whose folder is not there opens
        nothing; with the folder made first it does. (A server of its own, in a folder of its own: killed after.)"""
        if not shutil.which("tmux"):
            self.skipTest("no tmux")
        tmp = tempfile.mkdtemp(prefix="sf", dir="/tmp")  # (short: a socket path has a length limit)
        self.addCleanup(shutil.rmtree, tmp, True)
        socket = os.path.join(tmp, "d", "s")
        env = dict(os.environ)
        env.pop("TMUX", None)

        def new():
            return subprocess.run(["tmux", "-S", socket, "new-session", "-d", "-s", "x", "sleep 20"], env=env,
                                  capture_output=True, text=True, timeout=10)
        try:
            self.assertIn("error creating", new().stderr)
            self.assertFalse(os.path.exists(socket))
            asyncio.run(tmuxio.socket_folder(socket))
            self.assertEqual(new().returncode, 0)
            self.assertTrue(os.path.exists(socket))
        finally:
            subprocess.run(["tmux", "-S", socket, "kill-server"], env=env, capture_output=True, timeout=10)


if __name__ == "__main__":
    unittest.main()
