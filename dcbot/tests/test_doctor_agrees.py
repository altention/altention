"""ops/doctor.py says whether the bot will refuse to open conversations because of the installation's settings
(dcbot.open-settings, 3b 2.6e). The two conditions are written twice, in the bot's with_() and in doctor's
open_settings_problem() (one function for both comes with step 2.7): here both are fed the same values and must
come to the same."""
import contextlib
import importlib.util
import io
import json
import os
import sys
import tempfile
import time
import unittest
from unittest import mock

from bot import config, tmux_bridge
from tests import tmuxguard  # noqa: F401,E402  (no test reaches a real tmux server)
from tests.test_turn_end import Ch, Host
from altention.connectors.claude_code import launch  # noqa: E402 (bot/ puts the engine on the path)


def doctor():
    path = os.path.join(os.path.dirname(config.ROOT), "ops", "doctor.py")
    spec = importlib.util.spec_from_file_location("doctor_for_the_bots_test", path)
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, os.path.dirname(path))  # (doctor imports its neighbours by name)
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(os.path.dirname(path))
    return module


class Broken(Exception):
    pass


class TheSameAnswer(unittest.TestCase):
    VALUES = (None, "", "plan", "acceptEdits", "bypassPermissions", "everything", "Plan", " plan", 5, 0, True, False,
              ["plan"], [], {"a": 1}, {}, 1.5, "opus", "no-such-model")

    @classmethod
    def setUpClass(cls):
        cls.doctor = doctor()

    def bot_refuses(self, permission, model):
        def installation(setting):
            value = {"dcbot.default_permission": permission, "dcbot.default_model": model}[setting]
            if isinstance(value, Broken):
                raise value
            return value
        bridge = tmux_bridge.TmuxBridge(Host(Ch()))
        with mock.patch.object(tmux_bridge, "installation", installation), mock.patch("traceback.print_exc"):
            try:
                bridge.with_({})
            except tmux_bridge.NotOpened:
                return True
        return False

    def doctor_says(self, permission, model):
        found = {"valid": list(launch.permissions())}
        for key, value in (("permission", permission), ("model", model)):
            if isinstance(value, Broken):
                found[key + "_error"] = "Broken: %s" % value
            else:
                found[key] = value
        return self.doctor.open_settings_problem(found)

    def test_every_pair_of_values(self):
        refused = 0
        for permission in self.VALUES + (Broken("not JSON"),):
            for model in self.VALUES + (Broken("not JSON"),):
                bot, said = self.bot_refuses(permission, model), self.doctor_says(permission, model)
                self.assertEqual(bot, said is not None, (permission, model, said))
                refused += bot
        self.assertGreater(refused, 20, "the values do include ones the bot refuses")
        self.assertFalse(self.bot_refuses("plan", "opus"))

    def test_the_sentence_names_the_key_and_the_value(self):
        self.assertIn("dcbot.default_permission 是「everything」", self.doctor_says("everything", None))
        self.assertIn("dcbot.default_model 是「5」", self.doctor_says("plan", 5))
        self.assertIn("Broken: not JSON", self.doctor_says(Broken("not JSON"), None))

    def test_the_probe_reads_them_the_way_the_bot_does(self):
        """The bot: remote.alt().setting(name), which is altention.config.setting; the probe's script says the same
        call and the connector's own list."""
        from bot import remote
        import altention.config
        self.assertIs(remote.alt(), altention.config)
        script = self.doctor.PROBE if hasattr(self.doctor, "PROBE") else open(self.doctor.__file__, encoding="utf-8").read()
        self.assertIn('altconfig.setting("dcbot.default_" + _k)', script)
        self.assertIn("_launch.permissions()", script)


class WhatTheBotWrote(unittest.TestCase):
    """The three state files doctor reads are written here by the bot's own functions (note_late(), note_away(),
    note_parked()) into a throwaway state folder, read as the probe reads them, and handed to doctor. No shape is
    written by hand (manager 2026-10-14: 30b3a99 read two of them as bare lists; the bot writes {"at", "channels"},
    so each was "not what the bot writes" from the first time it was written, for good)."""

    @classmethod
    def setUpClass(cls):
        cls.doctor = doctor()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        p = mock.patch.object(config, "STATE", self.tmp.name)
        p.start()
        self.addCleanup(p.stop)
        self.ch = Ch()
        self.host = Host(self.ch)
        self.bridge = tmux_bridge.TmuxBridge(self.host)

    def read(self, name):
        """As the probe's rj(): the file's JSON, {} when it is not there."""
        try:
            with open(os.path.join(self.tmp.name, name)) as f:
                return json.load(f)
        except Exception:
            return {}

    def item(self, check_id, key, name):
        recs = self.doctor.bot_notes("lap", {"now": time.time(), key: self.read(name)})
        return next(r for r in recs if r["id"] == check_id)

    def test_panes_whose_conversation_was_moved_to_the_background(self):
        item = lambda: self.item("dcbot.panes-parked", "panes_parked", tmux_bridge.PARKED)  # noqa: E731
        self.assertEqual(item()["status"], "ok", "never written")
        st = self.host.chan(self.ch)
        st.pop("tmux", None)
        st["pane"] = {"socket": "/tmp/x/default", "id": "%7"}
        st["parked"] = {"session": "s-1", "since": time.time() - 600, "pane": tmux_bridge.window_of(st)}
        self.assertIsNotNone(tmux_bridge.parked(st), "the channel is one the bot holds as moved")
        self.bridge.note_parked()
        r = item()
        self.assertEqual((r["status"], r["value"]), ("warn", 1), r)
        self.assertIn("有 1 個頻道的窗格", r["msg"])
        self.assertIn(str(self.ch.id), r["msg"])
        self.assertNotIn("讀不懂", r["msg"])
        self.assertNotIn("沒有回音", r["msg"])
        st["parked"]["unanswered"] = time.time()
        self.bridge.note_parked()
        self.assertIn("其中 1 個最近一則訊息打進去後沒有回音（%s）" % self.ch.id, item()["msg"])
        st.pop("parked")
        self.bridge.note_parked()
        self.assertEqual(self.read(tmux_bridge.PARKED)["channels"], [], "written, then none any more: the bot writes the empty list")
        r = item()
        self.assertEqual((r["status"], r["msg"], r["value"]), ("ok", "", 0))

    def test_channels_discord_does_not_hand_out(self):
        item = lambda: self.item("dcbot.channels-unreachable", "channels_unreachable", tmux_bridge.UNREACHABLE)  # noqa: E731
        self.assertEqual(item()["status"], "ok", "never written")
        self.bridge.away["42"] = [time.time() - 7200, 7]
        self.bridge.note_away()
        r = item()
        self.assertEqual((r["status"], r["value"]), ("warn", 1), r)
        self.assertIn("42（從", r["msg"])
        self.assertIn("丟了 7 個事件", r["msg"])
        self.assertNotIn("讀不懂", r["msg"])
        self.assertIn("機器人不會自己關", r["fix"])
        self.assertNotIn("可以關", r["msg"] + r["fix"])
        self.bridge.away.clear()
        self.bridge.note_away()
        self.assertEqual(self.read(tmux_bridge.UNREACHABLE)["channels"], [])
        r = item()
        self.assertEqual((r["status"], r["msg"], r["value"]), ("ok", "", 0))

    def test_turn_ends_that_came_without_their_row(self):
        item = lambda now=None: next(r for r in self.doctor.bot_notes("lap", {  # noqa: E731
            "now": now or time.time(), "turn_ends_late": self.read(tmux_bridge.LATE_ENDS)}) if r["id"] == "dcbot.turn-ends-late")
        self.assertEqual(item()["status"], "ok", "never written")
        rt = self.bridge.rt(self.ch)
        with mock.patch.object(tmux_bridge.cc_hooks, "ends_late", return_value=3), contextlib.redirect_stdout(io.StringIO()):
            self.bridge.note_late(rt)
        r = item()
        self.assertEqual((r["status"], r["value"]), ("warn", 3), r)
        self.assertIn("有 3 輪", r["msg"])
        self.assertNotIn("讀不懂", r["msg"])
        self.assertEqual(item(now=time.time() + 8 * 86400)["status"], "ok", "a week on, nothing new: no longer shown")

    def test_the_probe_reads_those_very_files(self):
        with open(self.doctor.__file__, encoding="utf-8") as f:
            script = f.read()
        for key, name in (("panes_parked", tmux_bridge.PARKED), ("channels_unreachable", tmux_bridge.UNREACHABLE),
                          ("turn_ends_late", tmux_bridge.LATE_ENDS)):
            self.assertIn('o["%s"] = rj(os.path.join(H(ENGINE), "dcbot/state/%s"))' % (key, name), script)


if __name__ == "__main__":
    unittest.main()
