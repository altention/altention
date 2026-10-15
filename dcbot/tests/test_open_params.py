"""Step 2.4b: what a conversation is opened with (model, permission mode: the channel's, the installation's, or as
it always was), the reasons a conversation could not be opened (a fixed word and what it is about, beside the words
the channel is told), and the settings file left alone when it says the same."""
import os
import unittest
from unittest import mock

from bot import config, tmux_bridge
from altention.connectors.claude_code import launch
from tests import fakepane  # noqa: F401  (the guard, and no real installation's settings)
from tests.test_open_session import Base, Tmux

N = tmux_bridge.NotOpened


class Params(Base):
    def argv_of(self, tmux, settings=None):
        self.installation = lambda name: (settings or {}).get(name)  # Base.go() answers the lookup with this
        got = self.go(self.bridge.ensure(self.ch, self.st, self.rt), tmux)
        calls = tmux.did("start") + tmux.did("open_window")
        return got, (calls[-1][3] if calls[-1][0] == "start" else calls[-1][5]) if calls else None

    def pick(self, argv):
        return argv[argv.index("--model") + 1], argv[argv.index("--permission-mode") + 1]

    def test_as_it_always_was_when_nothing_is_set(self):
        got, argv = self.argv_of(Tmux(self))
        self.assertIsNone(got)
        self.assertEqual(argv, [config.CLAUDE, "--model", config.MODEL, "--settings", tmux_bridge.SETTINGS_FILE,
                                "--permission-mode", "bypassPermissions", "--append-system-prompt", "you are in #c"])

    def test_the_installations(self):
        got, argv = self.argv_of(Tmux(self), {"dcbot.default_model": "sonnet", "dcbot.default_permission": "manual"})
        self.assertIsNone(got)
        self.assertEqual(self.pick(argv), ("sonnet", "manual"))

    def test_the_channels_own_before_the_installations(self):
        self.st.update({"model": "haiku", "permission": "plan"})
        got, argv = self.argv_of(Tmux(self), {"dcbot.default_model": "sonnet", "dcbot.default_permission": "manual"})
        self.assertIsNone(got)
        self.assertEqual(self.pick(argv), ("haiku", "plan"))

    def test_each_by_itself(self):
        self.st.update({"permission": "acceptEdits"})
        _, argv = self.argv_of(Tmux(self), {"dcbot.default_model": "sonnet"})
        self.assertEqual(self.pick(argv), ("sonnet", "acceptEdits"))

    def test_in_a_window_and_on_another_machine_too(self):
        self.st.pop("tmux", None)
        for host in (None, "lap"):
            self.setUp()
            self.st.pop("tmux", None)
            self.st["home"] = dict({"socket": "/tmp/x/default", "session": "main"}, **({"host": host} if host else {}))
            self.st.update({"model": "haiku", "permission": "plan"})
            got, argv = self.argv_of(Tmux(self))
            self.assertIsNone(got)
            self.assertEqual(self.pick(argv), ("haiku", "plan"), host)
            self.assertEqual(argv[0], "claude" if host else config.CLAUDE)
            self.assertEqual("--settings" in argv, not host)

    def test_a_permission_mode_there_is_not_opens_nothing_and_says_which_there_are(self):
        for where, value in (("the channel's", "yolo"), ("the installation's", "everything")):
            self.setUp()
            tmux = Tmux(self)
            if where == "the channel's":
                self.st["permission"] = value
            err, argv = self.argv_of(tmux, {} if where == "the channel's" else {"dcbot.default_permission": value})
            self.assertIsInstance(err, N, where)
            whose = "channel" if where == "the channel's" else "installation"
            self.assertEqual((err.why, err.data), (N.PERMISSION, {"value": value, "valid": list(launch.permissions()), "whose": whose}))
            said = "這個頻道設定的權限模式" if whose == "channel" else "這個安裝設定的預設權限模式（plugins.json 的 dcbot.default_permission）"
            self.assertEqual(str(err), "%s「%s」不是可以用的值，沒有開對話。可以用的：%s。" % (said, value, "、".join(launch.permissions())),
                             "whose value it is: the channel has none of its own when it is the installation's (#code-review)")
            self.assertEqual(tmux.calls, [], "nothing was started, and not with the default instead")
            self.assertIsNone(argv)

    def test_values_that_are_no_words(self):
        """A file edited by hand: a number, a list. Neither a permission mode nor a model like that opens anything,
        and the channel is told whose value it is: never an error nobody catches (#code-review)."""
        for value in (7, ["plan"], {"a": 1}, True):
            self.setUp()
            tmux = Tmux(self)
            self.st["permission"] = value
            err, _ = self.argv_of(tmux)
            self.assertEqual((getattr(err, "why", None), tmux.calls), (N.PERMISSION, []), repr(value))
            for whose, st, settings in (("channel", {"model": value}, {}), ("installation", {}, {"dcbot.default_model": value})):
                self.setUp()
                tmux = Tmux(self)
                self.st.update(st)
                err, _ = self.argv_of(tmux, settings)
                self.assertEqual((getattr(err, "why", None), tmux.calls), (N.MODEL, []), (whose, value))
                self.assertEqual(err.data, {"value": value, "whose": whose})
                said = "這個頻道設定的模型" if whose == "channel" else "這個安裝設定的預設模型（plugins.json 的 dcbot.default_model）"
                self.assertEqual(str(err), "%s「%s」不是可以用的值（要是一個名稱，例如 opus），沒有開對話。" % (said, value))

    def test_broken_settings_are_not_waited_for_in_the_event_loop(self):
        """The engine's reading of a broken settings file waits 0.4 s the first time (time.sleep): in a thread then.
        One that reads well is read at once, with nothing else handled in between (in a thread always, the first
        version of this commit, tests/test_put_away failed 9 runs of 12: the old window's last word came in the
        gap and was told as this conversation's end)."""
        import threading
        for well in (True, False):
            self.setUp()
            seen = []
            self.installation = lambda name: seen.append(threading.current_thread() is threading.main_thread())
            with mock.patch.object(tmux_bridge, "settings_read_well", lambda: well):
                self.assertIsNone(self.go(self.bridge.ensure(self.ch, self.st, self.rt), Tmux(self)))
            self.assertEqual(seen, [well, well], "the model and the permission mode, once each")

    def test_which_settings_read_well(self):
        import json
        import shutil
        import tempfile
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        path = os.path.join(d, "plugins.json")
        with mock.patch.dict(os.environ, {"ALT_INSTANCE": d}):
            self.assertTrue(tmux_bridge.settings_read_well(), "no settings file")
            for text, well in ((json.dumps({"dcbot": {}}), True), ("{", False), ("[1]", False), ("", False)):
                with open(path, "w") as f:
                    f.write(text)
                self.assertEqual(tmux_bridge.settings_read_well(), well, repr(text))
        with mock.patch.dict(os.environ, {"ALT_INSTANCE": "-"}):
            self.assertTrue(tmux_bridge.settings_read_well(), "no instance")
        with mock.patch.dict(os.environ, {"ALT_INSTANCE": os.path.join(d, "none")}):
            self.assertFalse(tmux_bridge.settings_read_well(), "an instance that is not there")


class Reasons(Base):
    """Every way a conversation is not opened: the words as they were, and now a fixed word and data with them."""

    def test_the_bots_own_session(self):
        tmux = Tmux(self, lambda argv: "silent")
        tmux.view = " API Error: 401"
        err = self.go(self.bridge.ensure(self.ch, self.st, self.rt), tmux, secs=0.15)
        self.assertEqual((err.why, err.data), (N.NOT_STARTED, {"screen": " API Error: 401"}))
        err = self.go(self.bridge.ensure(self.ch, self.st, self.rt), Tmux(self, lambda argv: "exits"))
        self.assertEqual((err.why, err.data, str(err)), (N.NOT_STARTED, {"screen": ""}, "Claude 沒有啟動成功。"))

    def test_tmux_itself_missing(self):
        tmux = Tmux(self)
        tmux.opens = False
        err = self.go(self.bridge.ensure(self.ch, self.st, self.rt), tmux)
        self.assertEqual((err.why, err.data), (N.NO_TMUX, {"error": "No such file or directory: 'tmux'"}))

    def test_a_window_in_the_owners_tmux(self):
        self.st.pop("tmux", None)
        self.st["home"] = {"socket": "/tmp/x/default", "session": "main"}
        tmux = Tmux(self)
        tmux.opens = False
        err = self.go(self.bridge.ensure(self.ch, self.st, self.rt), tmux)
        self.assertEqual((err.why, err.data), (N.NO_WINDOW, {"session": "main", "said": "tmux said no"}))
        self.assertEqual(str(err), "在 tmux `main` 裡開不起 Claude。\n-# tmux said no")
        self.assertIsNone(self.rt.opening)

    FAR = {"socket": "/tmp/x/default", "id": "%7", "host": "lap"}

    def far(self, *answers):
        """An attached pane on another machine, asked once for each of `answers`: True (it runs Claude), False (its
        machine answered: it does not), None (the asking itself failed: ssh's 255). -> (the error or None, how
        often it was asked)."""
        self.st.pop("tmux", None)
        self.st["pane"] = dict(self.FAR)
        tmux, left, asked = Tmux(self), list(answers), []

        async def is_alive(t):
            asked.append(t["id"])
            said = left.pop(0)
            tmux_bridge.tmuxio.asked(t, answered=said is not None)
            return bool(said)
        tmux.alive = is_alive
        self.addCleanup(tmux_bridge.tmuxio.UNANSWERED.clear)
        with mock.patch.object(tmux_bridge.remote, "reach", mock.AsyncMock(side_effect=AssertionError("asked a second time round"))), \
                mock.patch.object(tmux_bridge.remote, "unreachable", lambda host: "連不上 %s（試過 lap-alias）" % host):
            return self.go(self.bridge.ensure(self.ch, self.st, self.rt), tmux), len(asked)

    def test_a_machine_that_cannot_be_reached_is_not_a_pane_that_runs_nothing(self):
        """#issues 29: the machine was off, and the channel was told its pane ran no Claude Code any more and to
        detach it. Whether it runs was never known. Asked once: what the asking tried is all there is to try
        (manager on d65da1a: one round, not two)."""
        err, asked = self.far(None)
        self.assertEqual(err.why, N.UNREACHABLE)
        self.assertEqual(err.data, {"host": "lap", "where": tmux_bridge.tmuxio.label(self.FAR), "said": "連不上 lap（試過 lap-alias）"})
        self.assertIn("連不上 lap（試過 lap-alias），所以不知道", str(err))
        self.assertIn("沒有送出", str(err))
        self.assertTrue(str(err).endswith("不需要 `!detach`。"), str(err))
        self.assertNotIn("已經沒有在跑", str(err))
        self.assertEqual(asked, 1)

    def test_a_machine_that_answers_now_and_then_never_has_its_pane_called_gone(self):
        """#code-review on d65da1a: a way to the machine found, and the asking right after it failed again: the
        pane was called gone, and the owner told to detach. An asking that got no answer is no fact about the pane."""
        for answers in ((None,), (None,), (True,), (None,), (True,), (None,), (None,), (True,), (None,), (None,)):
            err, _ = self.far(*answers)
            self.assertNotEqual(getattr(err, "why", None), N.PANE_GONE, answers)
            self.assertNotIn("已經沒有在跑", str(err or ""))
            self.assertEqual(err is None, answers == (True,))

    def test_answered_and_the_pane_runs_nothing(self):
        err, asked = self.far(False)
        self.assertEqual(err.why, N.PANE_GONE)
        self.assertIn("已經沒有在跑 Claude Code", str(err))
        self.assertEqual(asked, 1)

    def test_a_far_pane_that_answers_is_asked_once(self):
        err, asked = self.far(True)
        self.assertIsNone(err)
        self.assertEqual(asked, 1)

    def test_an_answer_takes_the_mark_of_no_answer_away(self):
        self.far(None)
        err, _ = self.far(False)
        self.assertEqual(err.why, N.PANE_GONE, "it answered this time: that it did not before says nothing now")

    def unanswered(self, tmux):
        async def is_alive(t):
            tmux_bridge.tmuxio.asked(t, answered=False)
            return False
        tmux.alive = is_alive
        self.addCleanup(tmux_bridge.tmuxio.UNANSWERED.clear)
        return tmux

    def test_a_window_the_bot_opened_there_is_not_opened_a_second_time(self):
        """A window on another machine whose machine does not answer may well be running: another one opened (the
        line back a moment later) would be a second Claude on the same conversation."""
        self.st.pop("tmux", None)
        self.st.update({"pane": dict(self.FAR, pid=77), "home": {"socket": "/tmp/x/default", "session": "main", "host": "lap"},
                        "session_id": "saved-1"})
        tmux = self.unanswered(Tmux(self))
        with mock.patch.object(tmux_bridge.remote, "unreachable", lambda host: "連不上 %s" % host):
            err = self.go(self.bridge.ensure(self.ch, self.st, self.rt), tmux)
        self.assertEqual(err.why, N.UNREACHABLE)
        self.assertEqual(tmux.calls, [], "no window opened, none closed")
        self.assertEqual((self.st["pane"]["id"], self.st["session_id"]), ("%7", "saved-1"), "and the channel keeps what it had")

    def test_no_message_to_send_again_where_none_was_being_sent(self):
        """Manager on d65da1a: the setup card, an archived channel taken back, dcpost --new-claude open the
        conversation without a message; "this one was not sent, send it again" is not what happened."""
        from bot import messages
        self.st.pop("tmux", None)
        self.st["pane"] = dict(self.FAR)
        tmux = self.unanswered(Tmux(self))
        with mock.patch.object(tmux_bridge.remote, "unreachable", lambda host: "連不上 %s" % host):
            self.assertFalse(self.go(self.bridge.start_now(self.ch), tmux))
        said = [m.content for m in self.ch.sent]
        self.assertEqual(len(said), 1, said)
        self.assertTrue(said[0].startswith("⚠️ 連不上 lap，所以這個頻道的對話還沒有開"), said)
        self.assertIn("在這個頻道發一則訊息就會開", said[0])
        self.assertNotIn("沒有送出", said[0])
        self.assertNotIn("再發一次", said[0])
        outcome = self.bridge.rt(self.ch).outcome
        self.assertEqual((outcome.code, messages.retry(outcome.code)), ("machine_unreachable_unopened", messages.TEMPORARY))

    def test_a_pane_on_this_machine_asks_no_other(self):
        self.st.pop("tmux", None)
        self.st["pane"] = {"socket": "/tmp/x/default", "id": "%7"}
        with mock.patch.object(tmux_bridge.remote, "reach", mock.AsyncMock(side_effect=AssertionError("asked another machine"))):
            err = self.go(self.bridge.ensure(self.ch, self.st, self.rt), Tmux(self))
        self.assertEqual(err.why, N.PANE_GONE)

    def test_the_channel_is_told_so_and_it_is_a_reason_that_passes(self):
        from bot import messages
        self.st.pop("tmux", None)
        self.st["pane"] = dict(self.FAR)

        class Message(object):
            channel, id, content = self.ch, 1, "hello"
        tmux = self.unanswered(Tmux(self))
        with mock.patch.object(tmux_bridge.remote, "unreachable", lambda host: "連不上 %s" % host):
            self.go(self.bridge.submit(Message(), "hello"), tmux)
        said = [m.content for m in self.ch.sent]
        self.assertEqual(len(said), 1, said)
        self.assertTrue(said[0].startswith("⚠️ 連不上 lap，所以不知道這個頻道接的 tmux 窗格還有沒有在跑 Claude Code"), said)
        self.assertNotIn("出錯了", said[0])
        outcome = self.bridge.rt(self.ch).outcome
        self.assertEqual((outcome.state, outcome.code), ("not_sent", "machine_unreachable"))
        self.assertEqual((messages.common(outcome.code), messages.retry(outcome.code)), ("not_ready", messages.TEMPORARY))
        self.assertEqual(tmux.calls, [], "nothing typed, nothing started")

    def test_any_other_reason_is_said_as_it_was(self):
        self.st.pop("tmux", None)
        self.st["pane"] = {"socket": "/tmp/x/default", "id": "%7"}

        class Message(object):
            channel, id, content = self.ch, 1, "hello"
        self.go(self.bridge.submit(Message(), "hello"), Tmux(self))
        said = [m.content for m in self.ch.sent]
        self.assertEqual(len(said), 1, said)
        self.assertTrue(said[0].startswith("出錯了：這個頻道接的 tmux 窗格已經沒有在跑 Claude Code 了"), said)
        self.assertEqual(self.bridge.rt(self.ch).outcome.code, "not_started")

    def test_an_attached_pane_that_runs_no_agent(self):
        self.st.pop("tmux", None)
        self.st["pane"] = {"socket": "/tmp/x/default", "id": "%7"}
        err = self.go(self.bridge.ensure(self.ch, self.st, self.rt), Tmux(self))
        self.assertEqual(err.why, N.PANE_GONE)
        self.assertIn("%7", err.data["where"])

    def test_they_are_all_the_error_they_were(self):
        self.assertTrue(issubclass(N, tmux_bridge.BridgeError))


class SettingsFile(unittest.TestCase):
    def test_the_bots_file_is_left_alone_when_it_says_the_same(self):
        import shutil
        import tempfile
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        with mock.patch.object(tmux_bridge, "SETTINGS_FILE", os.path.join(d, "claude-settings.json")), \
                mock.patch.object(tmux_bridge, "SPOOL", os.path.join(d, "events")):
            self.assertTrue(tmux_bridge.write_settings())
            self.assertTrue(os.path.isdir(os.path.join(d, "events")))
            self.assertFalse(tmux_bridge.write_settings())
            import json
            with open(os.path.join(d, "claude-settings.json")) as f:
                got = json.load(f)
        self.assertEqual(got["permissions"], {"deny": tmux_bridge.deny()})
        self.assertEqual(sorted(got["hooks"]), sorted(launch.settings([], "x")["hooks"]))


if __name__ == "__main__":
    unittest.main()
