"""altention dc notify (altention/dc/notify.py): the path problems are reported on does not depend on this machine's settings (2.4d-3)."""
import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))))
from altention import testing as _testing  # noqa: E402
_testing.guard_home()  # tests never touch the real home (altention/testing.py)
import importlib.machinery
import importlib.util
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(HERE))
from altention import config  # noqa: E402


def load():
    loader = importlib.machinery.SourceFileLoader("dcnotify_t", os.path.join(os.path.dirname(HERE), "altention", "dc", "notify.py"))
    mod = importlib.util.module_from_spec(importlib.util.spec_from_loader("dcnotify_t", loader))
    loader.exec_module(mod)
    return mod


class Broken(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.mod = load()
        self.mod.CACHE = os.path.join(self.tmp, "bot.json")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp)

    def send(self, argv, bot):
        sent = []

        def run(cmd, input=None, **kw):
            sent.append((cmd, json.loads(input.decode("utf-8"))))
            return mock.Mock(returncode=0, stdout=b"", stderr=b"")
        with mock.patch.object(config, "bot_machine", bot), mock.patch.object(self.mod.subprocess, "run", run), \
                mock.patch.object(sys, "argv", ["dcnotify"] + argv), mock.patch.dict(os.environ, {"DCNOTIFY_CHANNEL": ""}):
            with self.assertRaises(SystemExit) as e:
                self.mod.main()
        return e.exception.code, sent

    def test_unreadable_settings_still_send_through_the_bot_last_reached(self):
        code, sent = self.send(["sync failing"], lambda: ("host-a", {"ssh": ["A"]}, False))
        self.assertEqual(code, 0)
        self.assertEqual(sent[0][0][-2:], ["A", "~/'altention/engine'/dcbot/tools/dcctl post"])
        self.assertNotIn("channel", sent[0][1], "the bot's machine decides the default channel")

        def broken():
            raise config.ConfigError("plugins.json is not JSON")
        code, sent = self.send(["sync failing"], broken)
        self.assertEqual(code, 0, "still sent")
        self.assertEqual(sent[0][0][-2:], ["A", "~/'altention/engine'/dcbot/tools/dcctl post"])
        self.assertEqual(sent[0][1]["text"], "sync failing")

    def test_unreadable_settings_and_no_bot_reached_before_says_so(self):
        def broken():
            raise config.ConfigError("no list")
        with mock.patch.object(config, "bot_machine", broken):
            with self.assertRaises(SystemExit) as e:
                self.mod.bot_machine()
        self.assertIn("沒有上次找到的機器人", str(e.exception.code))


if __name__ == "__main__":
    unittest.main()
