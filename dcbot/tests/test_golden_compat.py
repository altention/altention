"""C-148 batch 4: golden of the bot's old state it still reads (spec §17.5): a route table entry as first written
(the channel id alone) beside today's. A change here is a change of how someone's old state is read
(tools/golden update <case>, and a "golden:" line in the commit)."""
import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))))
from altention import testing as _testing  # noqa: E402
_testing.guard_home()  # tests never touch the real home (altention/testing.py; 11-08: one left a real dcremote-owner)
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))  # the engine
from altention import testing  # noqa: E402
from bot import routecore  # noqa: E402
from tests import tmuxguard  # noqa: F401,E402  (no test reaches a real tmux server)

HERE = os.path.dirname(os.path.abspath(__file__))


class Routes(unittest.TestCase):
    def test_old_and_new_route_entries(self):
        table = {"s|%1": "111", "s|%2": {"channel": "222", "pid": "9"}, "s|%3": {"channel": "333", "pid": "9", "session": "ab12"},
                 "s|%4": None}
        rows = ["%s %r → channel %r, routed (pid 9) %r, routed (pid 8) %r\n"
                % (k, v, routecore.channel_of(v), routecore.routed_channel(table, k, "9"), routecore.routed_channel(table, k, "8"))
                for k, v in table.items()]
        testing.golden("compat/route-entries", "".join(rows), "§17.5", folder=HERE)


if __name__ == "__main__":
    unittest.main()
