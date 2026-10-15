"""remote.py: the commands sent to another machine name that machine's engine (its "engine" in the machine list,
default ~/altention/engine), checked by running them through a real sh with a throwaway HOME and fake tools."""
import os
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

from bot import remote
from tests import tmuxguard  # noqa: F401,E402  (no test reaches a real tmux server)


class RemoteEngine(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="家 home ")
        self.inv = {"machines": {"host-a": {"ssh": ["host-a"], "roles": ["dcattach"], "engine": "~/my 引擎"},
                                 "host-b": {"ssh": ["host-b"], "roles": ["dcattach"]}}}

    def tearDown(self):
        shutil.rmtree(self.home, ignore_errors=True)

    def sh(self, command):
        return subprocess.run(["sh", "-c", command], env={"HOME": self.home, "PATH": "/usr/bin:/bin"},
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True)

    def test_dcremote_on_each_machine(self):
        with mock.patch.object(remote, "inventory", return_value=self.inv):
            a, b = remote.dcremote_command("host-a"), remote.dcremote_command("host-b")
        self.assertEqual(b, "python3 ~/'altention/engine'/dcbot/tools/dcremote", "today's machines: the same folder")
        self.assertEqual(self.sh("printf %s " + a.split(" ", 1)[1]).stdout,
                         os.path.join(self.home, "my 引擎", "dcbot", "tools", "dcremote"))

    def test_the_stream_line_goes_into_that_engine_and_runs_its_tools(self):
        engine = os.path.join(self.home, "my 引擎")
        os.makedirs(os.path.join(engine, "dcbot", "tools"))
        with open(os.path.join(engine, "dcbot", "tools", "dcremote"), "w") as f:
            f.write("import os, sys; print('dcremote', sys.argv[1], os.getcwd())\n")
        with open(os.path.join(engine, "dcbot", "tools", "dchook"), "w") as f:
            f.write("")
        with mock.patch.object(remote, "inventory", return_value=self.inv):
            cmd = remote.stream_command("host-a")
        r = self.sh(cmd)
        self.assertIn("dcremote stream %s" % engine, r.stdout)

    def test_claude_is_told_the_full_path_of_altention_on_its_machine(self):
        """Manager 2026-10-14: never a bare `altention` (a Bash tool that is not a login shell may not find it)."""
        with mock.patch.object(remote, "inventory", return_value=self.inv), \
                mock.patch.object(remote, "this_machine", return_value="host-c"):
            a, b = remote.altention_cmd("host-a"), remote.altention_cmd("host-b")
            here = remote.altention_cmd(None)
        self.assertEqual(b, "~/altention/engine/bin/altention")
        self.assertEqual(self.sh("printf %s " + a).stdout, os.path.join(self.home, "my 引擎", "bin", "altention"),
                         "a path with a space is quoted for the shell, ~ left to it")
        self.assertTrue(here.endswith("/bin/altention") and os.path.isfile(os.path.expanduser(here.replace("'", ""))), here)

    def test_an_unreadable_list_falls_back_to_the_default(self):
        with mock.patch.object(remote, "inventory", side_effect=RuntimeError("broken")):
            self.assertEqual(remote.engine_of("host-a"), "~/altention/engine", "a fixed default: nothing to quote")


if __name__ == "__main__":
    unittest.main()
