"""dclease's commands over ssh name the primary's engine as remote.engine_of() gives it, ready for that shell: never
quoted once more (its ~ would be a folder named "~": ssh_veto never saw the bot alive, the fence was never written;
found in C-163 d2b). Run for real with sh -c in a scratch HOME too: ~ comes out as that HOME."""
import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))))
from altention import testing as _testing  # noqa: E402
_testing.guard_home()  # tests never touch the real home (altention/testing.py; 11-08: one left a real dcremote-owner)
import os
import subprocess
import tempfile
import unittest
from unittest import mock

from tests import tmuxguard  # noqa: F401  (no test reaches a real tmux server)
from tests.test_dclease import load_tool

ENGINE = "~/'altention/engine'"  # (what engine_of gives for the default: altention.config.engine_on)


class Commands(unittest.TestCase):
    def capture(self, fn, *args):
        tool = load_tool()
        sent = []
        with mock.patch.object(tool.remote, "engine_of", lambda host: ENGINE), \
                mock.patch.object(tool, "ssh", lambda host, cmd: (sent.append(cmd), (0, "5 03:00"))[1]):
            fn(tool)(*args)
        return sent[0]

    def test_the_engine_as_given(self):
        for name, fn, args in (("ssh_veto", lambda t: t.ssh_veto, ("host-a",)), ("fence", lambda t: t.fence, ("host-a", 4))):
            with self.subTest(name):
                cmd = self.capture(fn, *args)
                self.assertIn(ENGINE + "/dcbot/state", cmd)
                self.assertNotIn("'~/", cmd, "never quoted once more")

    def test_for_real_in_a_scratch_home(self):
        home = tempfile.mkdtemp()
        state = os.path.join(home, "altention", "engine", "dcbot", "state")
        os.makedirs(state)
        with open(os.path.join(state, "heartbeat"), "w") as f:
            f.write("1")
        env = dict(os.environ, HOME=home)
        veto = self.capture(lambda t: t.ssh_veto, "host-a")
        out = subprocess.run(["sh", "-c", veto], capture_output=True, text=True, env=env, timeout=30).stdout
        self.assertNotIn("NOSTATE", out, "~ came out as the HOME: the state folder found")
        fence = self.capture(lambda t: t.fence, "host-a", 4)
        subprocess.run(["sh", "-c", fence], env=env, timeout=30)
        with open(os.path.join(state, "fenced")) as f:
            self.assertEqual(f.read(), "4")


if __name__ == "__main__":
    unittest.main()
