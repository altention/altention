"""Sessions messaging each other: names behind channels, mentions spelled out, messages recognised."""
import json
import os
import tempfile
import unittest

from bot import peers
from tests import tmuxguard  # noqa: F401,E402  (no test reaches a real tmux server)


class Peers(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old = os.environ.get("CLAUDE_CONFIG_DIR")
        os.environ["CLAUDE_CONFIG_DIR"] = self.tmp.name
        os.makedirs(os.path.join(self.tmp.name, "sessions"))
        for pid, name, where in ((os.getpid(), "stock-a", "st:@22.%25"), (os.getppid(), "bot-one", "dc-x-1:@0.%0"),
                                 (999999999, "gone", "st:@24.%27")):
            with open(os.path.join(self.tmp.name, "sessions", "%d.json" % pid), "w") as f:
                json.dump({"pid": pid, "name": name, "tmux": where}, f)
        self.state = {"55": {"pane": {"socket": "/s", "id": "%25"}, "pane_session": "st"}, "66": {"tmux": "dc-x-1"},
                      "77": {"pane": {"socket": "/s", "id": "%27"}}}

    def tearDown(self):
        if self.old is None:
            del os.environ["CLAUDE_CONFIG_DIR"]
        else:
            os.environ["CLAUDE_CONFIG_DIR"] = self.old
        self.tmp.cleanup()

    def test_names_behind_channels(self):
        self.assertEqual(peers.directory(self.state), {"stock-a": 55, "bot-one": 66}, "a dead process has no name")
        self.assertEqual(peers.who("stock-a", self.state), "<#55>")
        self.assertEqual(peers.who("elsewhere", self.state), "`elsewhere`")

    def test_mentions_are_spelled_out(self):
        text = peers.spell_mentions("report to <#55>, not <#88>", self.state, lambda cid: {55: "#st-0"}.get(cid))
        self.assertIn("#st-0（這個頻道有一個 Claude Code 對話；貼東西過去或跟它說話：`altention dc post st-0 \"…\"`；"
                      "讀它最近的訊息：`altention dc read st-0`）", text)
        self.assertIn("not #88（貼東西過去或跟它說話", text)

    def test_incoming(self):
        self.assertEqual(peers.incoming('<cross-session-message from="uds:/x.sock" from-name="a-de" from-mode="bypass">\n'
                                        'Please reply.\nLine two.\n</cross-session-message>'), ("a-de", "Please reply.\nLine two."))
        self.assertIsNone(peers.incoming("hello"))
        self.assertIsNone(peers.incoming("<task-notification>x</task-notification>"))


if __name__ == "__main__":
    unittest.main()
