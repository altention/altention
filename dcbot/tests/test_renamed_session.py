"""C-201 6.3: a session of the bot's own tmux that the owner renamed is taken again by the channel it was opened for
(found by DCBOT_ROOT and DCBOT_CHANNEL in its Claude Code's own environment, as the reaper finds it), not a second
Claude Code opened on the same conversation under the old name. None found, or more than one: opened as before."""
import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))))
from altention import testing as _testing  # noqa: E402
_testing.guard_home()  # tests never touch the real home (altention/testing.py; 11-08: one left a real dcremote-owner)
import asyncio
import unittest

from bot import config, tmux_bridge
from tests import fakepane
from tests.test_reap import Server, session
from tests.test_turn_end import Ch, Host

OLD = "dc-c-0005"


class Renamed(unittest.TestCase):
    def setUp(self):
        self.ch = Ch()  # id 5
        self.host = Host(self.ch)
        self.st = self.host.channels_state["5"] = {"tmux": OLD, "cwd": "/w", "session_id": "aaaa1111-x"}
        self.bridge = tmux_bridge.TmuxBridge(self.host)
        self.opened = []

        async def opened(ch, st, rt, start, kept, failed, x):
            self.opened.append(st["tmux"])
            return True

        async def nothing(*a, **kw):
            return False
        self.bridge.open, self.bridge.print_done, self.bridge.foreign_window = opened, nothing, nothing

    def ensure(self, sessions):
        with fakepane.use(Server(sessions)):
            asyncio.run(self.bridge.ensure(self.ch, self.st, None))

    def test_a_renamed_session_is_taken_again(self):
        self.ensure({"mine": session(channel=5, idle=0)})
        self.assertEqual((self.opened, self.st["tmux"]), ([], "mine"))
        (said,) = self.host.audits
        self.assertIn("`%s` 在 tmux 被改名為 `mine`：照用改名後的，沒有另開" % OLD, said)
        self.ensure({"mine": session(channel=5, idle=0)})
        self.assertEqual((self.opened, len(self.host.audits)), ([], 1), "found by its name now: nothing more said")

    def test_the_session_there_by_its_name_is_used_as_before(self):
        self.ensure({OLD: session(channel=5, idle=0), "other": session(channel=5, idle=0, pid=501)})
        self.assertEqual((self.opened, self.st["tmux"], self.host.audits), ([], OLD, []))

    def test_opened_as_before_when_it_cannot_be_told(self):
        cases = (
            {},                                                                      # nothing on the server
            {"x": session(channel=6)},                                               # another channel's
            {"x": session(channel=5, root="/another/bot")},                          # another bot's
            {"x": session(channel=5, command="bash")},                               # not Claude Code
            {"x": session(channel=None)},                                            # not started by a bot
            {"x": session(channel=5), "y": session(channel=5, pid=501)},             # two: not guessed
            {"named": session(channel=5)},                                           # another channel's state names it
        )
        for sessions in cases:
            self.opened, self.host.audits = [], []
            self.st["tmux"] = OLD
            self.host.channels_state["7"] = {"tmux": "named"}
            self.ensure(sessions)
            self.assertEqual((self.opened, self.st["tmux"], self.host.audits), ([OLD], OLD, []), sessions)

    def test_the_bots_root_is_compared_as_a_real_path(self):
        self.ensure({"mine": session(channel=5, root=config.ROOT + "/")})
        self.assertEqual(self.st["tmux"], "mine")


if __name__ == "__main__":
    unittest.main()
