"""Step 2.4b: what a conversation is opened with (model, permission mode: the channel's, the installation's, or as
it always was), the reasons a conversation could not be opened (a fixed word and what it is about, beside the words
the channel is told), and the settings file left alone when it says the same."""
import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))))
from altention import testing as _testing  # noqa: E402
_testing.guard_home()  # tests never touch the real home (altention/testing.py; 11-08: one left a real dcremote-owner)
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

    def test_no_permission_mode_set_opens_nothing_and_says_how_to_set_one(self):
        """C-121: the last resort was bypassPermissions (everything let through). Now nothing is opened, on every
        way a conversation is opened, and the channel is told which value to set and how."""
        for settings in ({}, {"dcbot.default_permission": ""}, {"dcbot.default_permission": None},
                         {"dcbot.default_model": "sonnet"}):
            for where in ("bot", "window", "far"):
                self.setUp()
                if where != "bot":
                    self.st.pop("tmux", None)
                    self.st["home"] = dict({"socket": "/tmp/x/default", "session": "main"}, **({"host": "lap"} if where == "far" else {}))
                tmux = Tmux(self)
                err, argv = self.argv_of(tmux, settings)
                self.assertIsInstance(err, N, (settings, where))
                self.assertEqual((err.why, err.data), (N.PERMISSION_UNSET, {}), (settings, where))
                self.assertEqual(str(err), tmux_bridge.UNSET)
                self.assertIn("dcbot.default_permission", str(err))
                self.assertIn("#setup", str(err))
                self.assertEqual((tmux.calls, argv), ([], None), "nothing started, and never with bypassPermissions instead")

    def test_the_mode_written_is_the_one_opened_with(self):
        """An older installation's first reconcile writes the mode it ran with (bypassPermissions): it opens as it did."""
        got, argv = self.argv_of(Tmux(self), {"dcbot.default_permission": "bypassPermissions"})
        self.assertIsNone(got)
        copy = tmux_bridge.cc_launch.with_folders(fakepane.settings_file(), [os.path.join(fakepane.attachments_dir(), "5")])
        self.assertEqual(argv, [config.CLAUDE, "--model", config.MODEL, "--settings", copy,
                                "--permission-mode", "bypassPermissions", "--append-system-prompt", "you are in #c"])

    def test_the_channels_own_with_none_for_the_installation(self):
        self.st["permission"] = "manual"
        got, argv = self.argv_of(Tmux(self), {})
        self.assertIsNone(got)
        self.assertEqual(self.pick(argv), (config.MODEL, "manual"))

    def test_settings_that_cannot_be_read_never_open_with_bypass(self):
        def broken(name):
            raise ValueError("plugins.json: not JSON")
        self.installation = broken
        tmux = Tmux(self)
        err = self.go(self.bridge.ensure(self.ch, self.st, self.rt), tmux)
        self.assertEqual((getattr(err, "why", None), tmux.calls), (N.SETTINGS, []))
        self.st["permission"] = "plan"  # the channel's own needs no settings file
        self.installation = broken
        self.assertIsNone(self.go(self.bridge.ensure(self.ch, self.st, self.rt), Tmux(self)))

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
            given = argv[argv.index("--settings") + 1]
            self.assertEqual(given.startswith("{"), bool(host), "there: the folder alone, as JSON; here: a file")
            self.assertNotIn("--add-dir", argv)

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
            for whose, st, settings in (("channel", {"model": value}, {"dcbot.default_permission": "manual"}),
                                        ("installation", {}, {"dcbot.default_model": value, "dcbot.default_permission": "manual"})):
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
            self.installation = lambda name, seen=seen: seen.append(threading.current_thread() is threading.main_thread()) or \
                ("manual" if name == "dcbot.default_permission" else None)
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
        self.st["bg"] = 2
        err = self.go(self.bridge.ensure(self.ch, self.st, self.rt), Tmux(self))
        self.assertEqual(err.why, N.PANE_GONE)
        self.assertIn("%7", err.data["where"])
        self.assertNotIn("bg", self.st, "what ran under it ended with it")
        self.assertIn("claude --resume", str(err))
        self.assertIn("/attach", str(err))

    def test_they_are_all_the_error_they_were(self):
        self.assertTrue(issubclass(N, tmux_bridge.BridgeError))


class PaneFollowed(Base):
    """C-163 drill problem A, manager 11-09 (a): an attached pane whose pid is not the one recorded, the same pane
    number running Claude with the channel's own conversation: the record follows it, said in #audit."""
    PANE = {"socket": "/tmp/x/default", "id": "%7", "host": "lap", "pid": 111}

    def run(self, result=None):
        with mock.patch.object(tmux_bridge.tmuxio, "UNANSWERED", set()):
            return super().run(result)

    def ensure(self, now, sid, session_id="conv-1", pane=None, home=None):
        self.st.pop("tmux", None)
        self.st.update({"pane": dict(pane or self.PANE)})
        if home:
            self.st["home"] = home
        self.st.pop("session_id", None)
        if session_id:
            self.st["session_id"] = session_id
        tmux, audits, asked = Tmux(self), [], []

        async def pane_session_id(pane):
            asked.append(pane)
            if isinstance(sid, Exception):
                raise sid
            return sid

        async def audit(text):
            audits.append(text)
        self.bridge.host.pane_session_id, self.bridge.host.audit = pane_session_id, audit
        with mock.patch.object(tmux_bridge.tmuxio, "moved_on", mock.AsyncMock(return_value=now)), \
                mock.patch.object(tmux_bridge, "AGAIN_SECS", 0):
            err = self.go(self.bridge.ensure(self.ch, self.st, self.rt), tmux)
        return err, audits, asked

    def rebound(self):
        """bind_any() calls (go() puts an AsyncMock there: self.bound)."""
        return [(c.args[0]["pid"], c.args[0].get("host"), c.args[1], c.kwargs.get("tag", True)) for c in self.bound.await_args_list]

    def marked(self, **kw):
        """What moved_on() says of an adapter that marked its pane (C-198: @altention_agent, valid)."""
        return dict({"pid": "222", "session": "pa", "cwd": "/w", "tag": str(self.ch.id), "agent": "pi", "agent_session": ""}, **kw)

    def test_the_same_conversation_in_the_same_pane_is_followed(self):
        err, audits, asked = self.ensure({"pid": "222", "session": "pa", "cwd": "/w"}, "conv-1")
        self.assertIsNone(err)
        self.assertEqual(self.st["pane"], dict(self.PANE, pid=222))
        self.assertEqual((asked[0]["id"], asked[0]["session"], asked[0]["host"]), ("%7", "pa", "lap"))
        self.assertEqual(len(audits), 1)
        self.assertIn("111 → 222", audits[0])
        self.assertIn("conv-1", audits[0])
        self.assertEqual(self.rebound(), [(222, "lap", self.ch.id, False)], "the route follows too")

    def test_another_conversation_there_is_the_pane_gone_as_before(self):
        err, audits, _ = self.ensure({"pid": "222", "session": "pa", "cwd": "/w"}, "conv-2")
        self.assertEqual(err.why, N.PANE_GONE)
        self.assertEqual(self.st["pane"]["pid"], 111, "the record left as it was")
        self.assertEqual(audits, [])

    def test_a_conversation_not_known_is_said_as_being_made_sure_of(self):
        """Manager 11-20 (C-198 drill, the words): a new Claude there whose conversation is not known yet is not
        "runs no Claude Code any more"; not sent all the same."""
        err, _, _ = self.ensure({"pid": "222", "session": "pa", "cwd": "/w"}, None)
        self.assertEqual(err.why, N.PANE_GONE)
        self.assertIn("剛重開", str(err))
        self.assertIn("還在確認是不是同一個對話", str(err))
        self.assertNotIn("沒有在跑 Claude Code", str(err))
        err, _, _ = self.ensure({"pid": "222", "session": "pa", "cwd": "/w"}, "conv-2")
        self.assertIn("沒有在跑 Claude Code", str(err), "another conversation there: as before")

    def test_an_adapter_that_marked_its_pane_is_followed_by_the_tag_and_said_quietly(self):
        """C-198 (manager 11-18): an adapter's pane respawned writes no Claude Code process record; the pane's tag for
        this channel (kept through respawn-pane) and the adapter's own mark are the proof, said in the channel too,
        quietly. No Claude Code record is asked for."""
        for session_id, agent_session in (("conv-1", ""), (None, ""), ("conv-1", "conv-1"), (None, "pi-1")):
            with self.subTest(session_id=session_id, agent_session=agent_session):
                self.ch.sent = []
                err, audits, asked = self.ensure(self.marked(agent_session=agent_session), "conv-x", session_id=session_id)
                self.assertIsNone(err)
                self.assertEqual((self.st["pane"]["pid"], asked), (222, []))
                self.assertEqual(len(audits), 1)
                self.assertIn("111 → 222", audits[0])
                self.assertIn("換成 pi，照窗格上這個頻道的標籤和它自己留的標記接回", audits[0])
                said = [m for m in self.ch.sent if "窗格重開過" in (m.content or "")]
                self.assertEqual([(m.content, m.silent) for m in said], [("-# 這個頻道的窗格重開過，已經接回同一個窗格", True)])
                self.assertEqual(self.rebound(), [(222, "lap", self.ch.id, False)],
                                 "another machine's route written there (#code-review-2 on e30ad2b3)")

    def test_an_adapter_with_another_conversation_is_not_followed(self):
        err, audits, _ = self.ensure(self.marked(agent_session="pi-2"), None, session_id="pi-1")
        self.assertEqual(err.why, N.PANE_GONE, "C-201 2a's rules for another conversation (manager 11-18)")
        self.assertEqual((self.st["pane"]["pid"], audits, self.rebound()), (111, [], []))

    def test_no_mark_is_never_followed_by_the_tag(self):
        """Manager 11-18: nothing guessed: no Claude Code record and no adapter's mark is the pane gone, as before;
        the record that could not be read is asked once more first."""
        tagged = {"pid": "222", "session": "pa", "cwd": "/w", "tag": str(self.ch.id), "agent": "", "agent_session": ""}
        for sid, times in ((None, 1), (RuntimeError("no answer"), 2)):
            with self.subTest(sid=sid):
                self.ch.sent = []
                err, audits, asked = self.ensure(tagged, sid)
                self.assertEqual(err.why, N.PANE_GONE)
                self.assertEqual((self.st["pane"]["pid"], audits, self.rebound()), (111, [], []))
                self.assertEqual(len(asked), times)
                self.assertFalse([m for m in self.ch.sent if "窗格重開過" in (m.content or "")])

    def test_a_pane_of_this_machine_followed_by_the_tag_takes_the_route_and_its_events_come_in(self):
        """#code-review-2 on e30ad2b3: the route kept the old pid, so the new process's events were a stranger's."""
        import tempfile
        from bot import routecore, routes
        d = tempfile.mkdtemp()
        self.addCleanup(__import__("shutil").rmtree, d, True)
        local = {"socket": "/tmp/x/default", "id": "%7", "pid": 111}
        with mock.patch.object(routecore, "ROUTES", os.path.join(d, "routes.json")):
            routes.bind(local, self.ch.id)
            err, _, _ = self.ensure(self.marked(), None, session_id=None, pane=local)
            self.assertIsNone(err)
            self.assertEqual(routecore.load(), {"/tmp/x/default|%7": {"channel": str(self.ch.id), "pid": "222"}})

    def test_a_window_this_bot_opened_here_writes_no_route(self):
        """#code-review low on b6420e2d: a window the bot opened in this machine's tmux reports through its
        --settings; its route table entry is left as it is (as remember_pids())."""
        import tempfile
        from bot import routecore
        d = tempfile.mkdtemp()
        self.addCleanup(__import__("shutil").rmtree, d, True)
        local = {"socket": "/tmp/x/default", "id": "%7", "pid": 111}
        st = {"pane": local, "home": {"session": "work"}, "cwd": "/w"}

        async def audit(text):
            pass
        self.bridge.host.audit = audit
        with mock.patch.object(routecore, "ROUTES", os.path.join(d, "routes.json")), \
                mock.patch.object(tmux_bridge.tmuxio, "moved_on", mock.AsyncMock(return_value=self.marked())):
            import asyncio
            self.assertTrue(asyncio.run(self.bridge.pane_again(self.ch, st)))  # (the look asks it: ensure() opens home windows apart)
            self.assertEqual(st["pane"]["pid"], 222)
            self.assertEqual(routecore.load(), {})
            st = {"pane": dict(local, host="lap"), "home": {"session": "work", "host": "lap"}, "cwd": "/w"}
            with mock.patch.object(tmux_bridge.routes, "bind_any", mock.AsyncMock()) as bound:
                self.assertTrue(asyncio.run(self.bridge.pane_again(self.ch, st)))
            self.assertEqual(bound.await_count, 1, "one opened on another machine: its route written there")

    def test_its_conversation_read_and_another_is_not_followed_whatever_its_tag(self):
        err, audits, _ = self.ensure({"pid": "222", "session": "pa", "cwd": "/w", "tag": str(self.ch.id), "agent": ""}, "conv-2")
        self.assertEqual(err.why, N.PANE_GONE, "another conversation: C-201 2a's rules, not the tag (manager 11-18)")
        self.assertEqual(self.st["pane"]["pid"], 111)
        self.assertEqual(audits, [])

    def test_marked_and_tagged_for_another_channel_or_none_is_the_pane_gone(self):
        for tag in ("99", ""):
            with self.subTest(tag=tag):
                err, audits, _ = self.ensure(self.marked(tag=tag), None)
                self.assertEqual(err.why, N.PANE_GONE)
                self.assertEqual(self.st["pane"]["pid"], 111)
                self.assertEqual(audits, [])

    def test_no_claude_there_is_the_pane_gone(self):
        err, _, asked = self.ensure(None, "conv-1")
        self.assertEqual(err.why, N.PANE_GONE)
        self.assertEqual(asked, [], "nothing more asked")


class MovedOn(unittest.TestCase):
    def ask(self, pane, out, under=None, agent=""):
        import asyncio
        from bot import tmuxio

        async def tmux(*a, **k):
            return 0, out
        with mock.patch.object(tmuxio, "tmux", tmux), mock.patch.object(tmuxio, "claude_under", mock.AsyncMock(return_value=under)), \
                mock.patch.object(tmuxio, "marked_agent", mock.AsyncMock(return_value=agent)), \
                mock.patch.object(tmuxio, "UNANSWERED", set()):
            return asyncio.run(tmuxio.moved_on(pane))

    def test_moved_on(self):
        pane = {"socket": "/tmp/x/default", "id": "%7", "pid": 111}
        self.assertEqual(self.ask(pane, "claude\t222\tpa\t/w\t\n"), {"pid": "222", "session": "pa", "cwd": "/w", "tag": "",
                                                                    "agent": "", "agent_session": ""})
        self.assertEqual(self.ask(pane, "python3\t222\tpa\t/w\t5\tpi|222\tpi-1\n", under=False, agent="pi"),
                         {"pid": "222", "session": "pa", "cwd": "/w", "tag": "5", "agent": "pi", "agent_session": "pi-1"},
                         "an adapter's valid mark: no Claude needed (C-198)")
        self.assertEqual(self.ask(pane, "claude\t222\tpa\t/w\t5\n")["tag"], "5", "the channel it is tagged for (C-198)")
        self.assertIsNone(self.ask(pane, "claude\t111\tpa\t/w\n"), "the same pid: nothing moved")
        self.assertIsNone(self.ask(pane, "bash\t222\tpa\t/w\n", under=False), "a shell there")
        self.assertIsNone(self.ask(dict(pane, pid=None), "claude\t222\tpa\t/w\n"), "no pid recorded")
        self.assertIsNone(self.ask(dict(pane, shell=True), "claude\t222\tpa\t/w\n"))


class MarkedAgent(unittest.TestCase):
    """C-198 (manager 11-18): an adapter's mark "<agent>|<pid>" counts only when its pid is the pane's process or one
    under it: a mark left before a respawn-pane names a process no longer the pane's."""

    def test_this_machine(self):
        import asyncio
        from bot import tmuxio
        me, parent = os.getpid(), os.getppid()
        cases = (("pi|%d" % me, me, "pi"), ("codex|%d" % me, parent, "codex"), ("pi|%d" % parent, me, ""),
                 ("pi|999999999", me, ""), ("pi", me, ""), ("|%d" % me, me, ""), ("", me, ""), ("pi|x", me, ""),
                 ("a|b|%d" % me, me, "a|b"))
        for mark, pane_pid, want in cases:
            with self.subTest(mark=mark, pane_pid=pane_pid):
                self.assertEqual(asyncio.run(tmuxio.marked_agent({"id": "%7"}, str(pane_pid), mark)), want)

    def test_a_shell_named_by_the_mark_does_not_count(self):
        """#code-review-2 low on 6d91c5b7 (manager: fixed now): a mark naming a shell (set from an interactive shell,
        or a wrapper's $$ with no exec) would keep the shell "an adapter" after the adapter ended, typed into."""
        import asyncio
        import subprocess
        from bot import tmuxio
        me = os.getpid()
        for argv, want in ((["bash", "-c", "sleep 30; :"], ""), (["sh", "-c", "sleep 30; :"], ""), (["sleep", "30"], "pi")):
            with self.subTest(argv=argv[0]):
                p = subprocess.Popen(argv)
                self.addCleanup(p.wait)
                self.addCleanup(p.kill)
                import time
                for _ in range(100):  # (its own name once it has exec'd)
                    if tmuxio.cc_process.name_of(p.pid) == argv[0]:
                        break
                    time.sleep(0.02)
                self.assertEqual(asyncio.run(tmuxio.marked_agent({"id": "%7"}, str(me), "pi|%d" % p.pid)), want)
        with mock.patch.object(tmuxio.cc_process, "name_of", lambda pid: "zsh"):
            self.assertEqual(asyncio.run(tmuxio.marked_agent({"id": "%7"}, str(me), "pi|%d" % me)), "")
        for shell in ("dash", "fish"):
            self.assertIn(shell, tmuxio.cc_process.SHELLS)

    def test_another_machine(self):
        import asyncio
        from bot import remote, tmuxio
        for code, out, want in ((0, "1\n", "pi"), (0, "0\n", ""), (1, "", None), (255, "", None)):  # (no answer: not
            # known, None; manager 11-20, both reviews of b641f556)
            with self.subTest(code=code, out=out):
                ask = mock.AsyncMock(return_value=(code, out))
                with mock.patch.object(remote, "dcremote", ask):
                    self.assertEqual(asyncio.run(tmuxio.marked_agent({"id": "%7", "host": "lap"}, "222", "pi|333")), want)
                self.assertEqual(ask.await_args.args, ("lap", "pid-under", "222", "333"))


class AliveByMark(unittest.TestCase):
    """C-198 (#code-review-2 on fffc1f4a, manager 11-18): alive() takes an adapter by its valid mark too, judged by the
    same marked_agent() as moved_on(); a Claude needs no mark, and a pane without one is asked nothing more."""

    def alive(self, out, under, agent):
        import asyncio
        from bot import tmuxio

        async def tmux(*a, **k):
            return 0, out
        marked = mock.AsyncMock(return_value=agent)
        with mock.patch.object(tmuxio, "tmux", tmux), mock.patch.object(tmuxio, "claude_under", mock.AsyncMock(return_value=under)), \
                mock.patch.object(tmuxio, "marked_agent", marked), mock.patch.object(tmuxio, "UNANSWERED", set()):
            got = asyncio.run(tmuxio.alive({"socket": "/tmp/x/default", "id": "%7", "pid": 222}))
        return got, [c.args[1:] for c in marked.await_args_list]

    def test_alive(self):
        self.assertEqual(self.alive("node\t222\tpi|222\n", False, "pi"), (True, [("222", "pi|222", "node")]))
        self.assertEqual(self.alive("node\t222\tpi|111\n", False, ""), (False, [("222", "pi|111", "node")]), "a mark not valid")
        self.assertEqual(self.alive("node\t222\t\n", False, ""), (False, [("222", "", "node")]))
        self.assertEqual(self.alive("claude\t222\t\n", None, ""), (True, []), "Claude itself: no mark asked")
        self.assertEqual(self.alive("node\t222\n", True, ""), (True, []), "Claude under it")
        self.assertEqual(self.alive("node\t333\tpi|333\n", False, "pi"), (False, []), "another pid than the recorded one")

    def test_back_at_a_shell_is_not_alive_whatever_the_mark(self):
        """#code-review low 1 on 6d91c5b7 (manager): tmux's foreground program a shell: not taken by the mark."""
        import asyncio
        from bot import tmuxio
        for shell in ("bash", "zsh", "sh", "dash", "fish", "-bash"):
            with self.subTest(shell=shell), mock.patch.object(tmuxio.cc_process, "adapter_pid", lambda top, pid: True):
                self.assertEqual(asyncio.run(tmuxio.marked_agent({"id": "%7"}, "222", "pi|222", shell)), "")
        with mock.patch.object(tmuxio.cc_process, "adapter_pid", lambda top, pid: True):
            self.assertEqual(asyncio.run(tmuxio.marked_agent({"id": "%7"}, "222", "pi|222", "node")), "pi")


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
