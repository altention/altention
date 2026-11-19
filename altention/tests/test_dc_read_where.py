"""C-198: `altention dc read --map` (or --where) asks the bot's machine which channel a pane is, from any machine:
the pane named by this machine's name, the socket tmux gives ($TMUX) and the pane ($TMUX_PANE). A program in a pane
on another machine (an adapter) asks this way, not its own machine's dcctl, whose state has no channels."""
import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))))
from altention import testing as _testing  # noqa: E402
_testing.guard_home()  # tests never touch the real home (altention/testing.py)
import os
import unittest
from unittest import mock

from altention.dc import read


class WhoIsAsking(unittest.TestCase):
    def args(self, env, host="hostb.lan"):
        with mock.patch.dict(os.environ, env, clear=True), mock.patch.object(read.socket, "gethostname", lambda: host):
            return read.dcctl_args(["--map"]), read.dcctl_args(["--where"])

    def test_a_pane_on_another_machine_is_named_by_that_machine(self):
        got = self.args({"TMUX": "/tmp/tmux-1000/adapterlab,4242,0", "TMUX_PANE": "%0"})
        self.assertEqual(got, (["map", "--pane", "hostb|/tmp/tmux-1000/adapterlab|%0"],) * 2)

    def test_a_window_the_bot_opened_says_its_channel(self):
        got = self.args({"DCBOT_CHANNEL": "42", "TMUX": "/tmp/tmux-1000/default,1,0", "TMUX_PANE": "%3"})
        self.assertEqual(got, (["map", "--me", "42"],) * 2)

    def test_outside_tmux_the_whole_server(self):
        self.assertEqual(self.args({}), (["map"],) * 2)


if __name__ == "__main__":
    unittest.main()
