"""C-188 (manager 11-16): posts always go to the machine whose bot runs. dcremote spool-out / spool-drop / spool-in
move them between spools (state/posts only, names kept: their order too); dcctl tells a Claude whose post waits
while bot-host was just rewritten that the bot is changing machines, not down."""
import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))))
from altention import testing as _testing  # noqa: E402
_testing.guard_home()  # tests never touch the real home (altention/testing.py)
import importlib.machinery
import importlib.util
import io
import json
import os
import tempfile
import time
import unittest
from unittest import mock

from tests import tmuxguard  # noqa: F401  (no test reaches a real tmux server)

NAME = "%020d-1.json"  # a spool file's name (postbox.write()'s shape; written out it looks like an id)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load(name, path):
    loader = importlib.machinery.SourceFileLoader(name, path)
    mod = importlib.util.module_from_spec(importlib.util.spec_from_loader(name, loader))
    loader.exec_module(mod)
    return mod


class Spool(unittest.TestCase):
    def setUp(self):
        self.r = load("dcremote_spool", os.path.join(ROOT, "tools", "dcremote"))
        self.state = tempfile.mkdtemp()
        p = mock.patch.object(self.r, "STATE", self.state)
        p.start()
        self.addCleanup(p.stop)

    def files(self):
        out = {}
        for root, _dirs, names in os.walk(self.state):
            for n in names:
                out[os.path.relpath(os.path.join(root, n), self.state)] = None
        return sorted(out)

    def test_in_out_drop_touch_state_posts_only(self):
        items = [{"name": (NAME % 2), "post": {"text": "b"}},
                 {"name": (NAME % 1), "post": {"text": "a"}},
                 {"name": "../../evil.json", "post": {"text": "x"}}, {"name": "x.json", "post": {}},
                 {"name": (NAME % 3), "post": "not a post"}, "junk"]
        self.assertEqual(self.r.spool_in(items), 2)
        self.assertEqual(self.files(), ["posts/" + NAME % 1, "posts/" + NAME % 2])
        with open(os.path.join(self.state, "posts", "zz-broken.json"), "w") as f:
            f.write("{")
        self.assertEqual(self.r.spool_out(), [{"name": (NAME % 1), "post": {"text": "a"}},
                                              {"name": (NAME % 2), "post": {"text": "b"}}],
                         "oldest first; others left out")
        self.assertEqual(self.r.spool_drop([(NAME % 1), "../posts/" + NAME % 2,
                                            (NAME % 9)]), 1)
        self.assertEqual(self.files(), ["posts/" + NAME % 2, "posts/zz-broken.json"])

    def test_through_main_as_the_bot_calls_it(self):
        out = io.StringIO()
        with mock.patch("sys.stdin", io.StringIO(json.dumps([{"name": (NAME % 5), "post": {"t": 1}}]))), \
                mock.patch("sys.stdout", out):
            self.r.main(["spool-in"])
        self.assertEqual(json.loads(out.getvalue()), {"posts": 1})
        out = io.StringIO()
        with mock.patch("sys.stdout", out):
            self.r.main(["spool-out"])
        self.assertEqual(json.loads(out.getvalue())[0]["name"], (NAME % 5))
        out = io.StringIO()
        with mock.patch("sys.stdout", out):
            self.r.main(["spool-drop", (NAME % 5)])
        self.assertEqual(json.loads(out.getvalue()), {"dropped": 1})


class Words(unittest.TestCase):
    def test_bot_host_just_written_is_moving(self):
        from altention import config
        home = tempfile.mkdtemp()
        with mock.patch.dict(os.environ, {"HOME": home}):
            self.assertFalse(config.bot_host_moving(), "no bot-host: not moving")
            config.write_bot_host("host-a", 4)
            self.assertTrue(config.bot_host_moving())
            self.assertFalse(config.bot_host_moving(now=time.time() + config.BOT_HOST_NEW + 5))

    def test_a_post_not_answered_while_moving(self):
        d = load("dcctl_moving", os.path.join(ROOT, "tools", "dcctl"))
        from bot import postbox
        err = io.StringIO()
        with mock.patch.object(d, "bot_moving", lambda: True), mock.patch.object(postbox, "take_receipt", lambda t: None), \
                mock.patch("sys.stderr", err):
            with self.assertRaises(SystemExit) as e:
                d.wait_for({"receipt": "r1"}, 0)
        self.assertEqual(e.exception.code, d.QUEUED)
        self.assertIn("機器人正在換主機，這則已經排著，切換完會處理，不用重送", err.getvalue())
        self.assertNotIn("沒在跑", err.getvalue())


if __name__ == "__main__":
    unittest.main()
