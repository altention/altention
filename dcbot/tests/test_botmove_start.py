"""C-110: the bot moves the old settings file once it is on Discord, in the background (bot/main.py move_settings,
settle_settings): whatever the move does, nothing is raised; a line the same as the last one said is not said again,
and while the old file waits it is tried again."""
import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))))
from altention import testing as _testing  # noqa: E402
_testing.guard_home()  # tests never touch the real home (altention/testing.py; 11-08: one left a real dcremote-owner)
import asyncio
import os
import tempfile
import unittest
from unittest import mock

from bot import config, main
from tests import tmuxguard  # noqa: F401,E402  (no test reaches a real tmux server)


class Start(unittest.TestCase):
    def setUp(self):
        self.state = tempfile.mkdtemp(prefix="dcbot-state ")
        self.addCleanup(lambda: __import__("shutil").rmtree(self.state, True))
        p = mock.patch.object(config, "STATE", self.state)
        p.start()
        self.addCleanup(p.stop)

    def test_a_line_is_said_once_then_not_again(self):
        from altention import botmove
        with mock.patch.object(botmove, "move", return_value=(1, "沒有搬（原因）", False)), mock.patch("builtins.print"):
            self.assertEqual(main.move_settings(), ("沒有搬（原因）", False))
            self.assertEqual(main.move_settings(), ("", False), "the same as the last one said: not again")
        with mock.patch.object(botmove, "move", return_value=(0, "搬好了", True)), mock.patch("builtins.print"):
            self.assertEqual(main.move_settings(), ("搬好了", True))

    def test_nothing_the_move_raises_goes_further(self):
        from altention import botmove
        with mock.patch.object(botmove, "move", side_effect=RuntimeError("secret-ish")), mock.patch("builtins.print"):
            line, done = main.move_settings()
        self.assertIn("搬的時候出錯：RuntimeError", line)
        self.assertNotIn("secret-ish", line)
        self.assertTrue(done)

    def test_no_old_file_nothing_said(self):
        from altention import botmove
        with mock.patch.object(botmove, "move", return_value=(0, "", True)):
            self.assertEqual(main.move_settings(), ("", True))
        self.assertFalse(os.path.exists(os.path.join(self.state, "botmove-said")))

    def test_waiting_is_tried_again_and_each_new_line_said(self):
        from altention import botmove
        said = []

        class Bot:
            async def audit(self, text):
                said.append(text)
        results = [(0, "複製了", False), (0, "複製了", False), (0, "搬好了", True)]
        with mock.patch.object(botmove, "move", side_effect=lambda: results.pop(0)), mock.patch("builtins.print"), \
                mock.patch.object(main, "MOVE_EVERY", 0):
            asyncio.run(main.Bot.settle_settings(Bot()))
        self.assertEqual(said, ["複製了", "搬好了"])
        self.assertEqual(results, [])


if __name__ == "__main__":
    unittest.main()
