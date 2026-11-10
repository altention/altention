"""!talklimit says that between two channels the larger of their limits counts (#discord-control set one side to 2
and the pair went on to 10; words: #release 11-03)."""
import unittest

from bot import main, posts
from tests import tmuxguard  # noqa: F401,E402  (no test reaches a real tmux server)


class Said(unittest.TestCase):
    def test_a_limit_and_how_a_pair_counts(self):
        self.assertEqual(main.talk_limit_said({"talk_limit": 2}, 10),
                         "這個頻道的上限：最多 2 次（你一插話就重新計算）。兩個頻道互傳時，以兩邊上限比較大的為準（沒設的頻道是 10 次），"
                         "任一邊設成 off 就不限制。要在 2 次停下，對方頻道也要設成 2 次以下。")

    def test_the_installations_own_default(self):
        self.assertIn("沒設的頻道是 30 次", main.talk_limit_said({}, 30))
        self.assertIn("最多 30 次", main.talk_limit_said({}, 30))
        self.assertIn("沒設的頻道不限制", main.talk_limit_said({"talk_limit": 5}, 0), "an installation that chose no limit")

    def test_off_as_before(self):
        self.assertEqual(main.talk_limit_said({"talk_limit": 0}, 10), "這個頻道的 Claude 跟別的頻道的 Claude 連續來回：不限制。")
        self.assertEqual(main.talk_limit_said({}, 0), "這個頻道的 Claude 跟別的頻道的 Claude 連續來回：不限制。")

    def test_the_channels_let_go(self):
        self.assertTrue(main.talk_limit_said({"talk_limit": 2, "talk_free": [7]}, 10).endswith("跟這些頻道不限制： <#7>"))

    def test_what_it_says_is_what_pair_limit_does(self):
        state = {"1": {"talk_limit": 2}, "2": {}}
        self.assertEqual(posts.pair_limit(state, 1, 2), posts.default_limit(), "one side alone: the other's default counts")
        state["2"]["talk_limit"] = 2
        self.assertEqual(posts.pair_limit(state, 1, 2), 2)


if __name__ == "__main__":
    unittest.main()
