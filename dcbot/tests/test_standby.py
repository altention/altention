"""C-163 step 3b: the standby's copy of the bot's state (bot/standby.py, dcremote state-in)."""
import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))))
from altention import testing as _testing  # noqa: E402
_testing.guard_home()  # tests never touch the real home (altention/testing.py; 11-08: one left a real dcremote-owner)
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest

from bot import standby
from tests import tmuxguard  # noqa: F401  (no test reaches a real tmux server)

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class Bundle(unittest.TestCase):
    def setUp(self):
        self.state = tempfile.mkdtemp()
        for name, body in (("channels.json", '{"1": {}}'), ("seen.json", "{}"), ("heartbeat", "1"), ("bot.pid", "2")):
            with open(os.path.join(self.state, name), "w") as f:
                f.write(body)
        os.makedirs(os.path.join(self.state, "sources"))
        with open(os.path.join(self.state, "sources", "9.json"), "w") as f:
            f.write("{}")

    def test_only_what_a_takeover_needs_goes_and_comes_out_whole(self):
        text = standby.bundle(self.state, {"at": 5, "engine": "abc"})
        target = os.path.join(tempfile.mkdtemp(), "state-standby")
        names = standby.unpack(text, target)
        self.assertEqual(sorted(names), ["channels.json", "meta.json", "seen.json", "sources/9.json"])
        self.assertFalse(os.path.exists(os.path.join(target, "heartbeat")), "each machine's own stays its own")
        standby.unpack(standby.bundle(self.state, {"at": 6}), target)  # again: in place, whole
        with open(os.path.join(target, "meta.json")) as f:
            self.assertEqual(json.load(f)["at"], 6)
        self.assertEqual([n for n in os.listdir(os.path.dirname(target)) if n != "state-standby"], [], "no leftovers")

    def test_the_folder_ignores_itself(self):
        """v0.6.0 B9: state-standby/ carries its own .gitignore ("*"), not a line in dcbot/.gitignore (a change there
        makes a rollback only partial); it comes back with every push, and is not one of the names written."""
        root = tempfile.mkdtemp()
        subprocess.run(["git", "init", "-q", root], check=True)
        target = os.path.join(root, "dcbot", "state-standby")
        names = standby.unpack(standby.bundle(self.state, {"at": 5}), target)
        self.assertNotIn(".gitignore", names)
        with open(os.path.join(target, ".gitignore")) as f:
            self.assertEqual(f.read(), "*\n")
        out = subprocess.run(["git", "-C", root, "status", "--porcelain", "--untracked-files=all"],
                             stdout=subprocess.PIPE, universal_newlines=True, check=True).stdout
        self.assertEqual(out, "", "git sees nothing of it")
        standby.unpack(standby.bundle(self.state, {"at": 6}), target)
        self.assertTrue(os.path.isfile(os.path.join(target, ".gitignore")), "put back with each push")

    def test_dcbot_gitignore_has_no_state_standby_line(self):
        """v0.6.0 B9: dcbot/.gitignore stays as v0.5.4 had it."""
        with open(os.path.join(HERE, ".gitignore")) as f:
            self.assertNotIn("state-standby", f.read())

    def test_no_name_outside(self):
        import base64
        import io
        import tarfile
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tar:
            for name in ("../evil", "/abs", "ok.json"):
                info = tarfile.TarInfo(name)
                info.size = 2
                tar.addfile(info, io.BytesIO(b"{}"))
        target = os.path.join(tempfile.mkdtemp(), "state-standby")
        self.assertEqual(standby.unpack(base64.b64encode(buf.getvalue()).decode(), target), ["ok.json"])

    def test_routes_stay_each_machines_own(self):
        """Drill item 12 on the mac (11-10): the primary's routes.json taken in took the place of the standby's, and
        an attached pane of the standby went silent after the takeover. Neither pushed nor taken in now."""
        with open(os.path.join(self.state, "routes.json"), "w") as f:
            json.dump({"/primary-socket|%0": {"channel": "1"}}, f)
        target = os.path.join(tempfile.mkdtemp(), "state-standby")
        standby.unpack(standby.bundle(self.state, standby.meta(HERE, "host-a")), target)
        self.assertFalse(os.path.exists(os.path.join(target, "routes.json")), "not in the push")
        with open(os.path.join(target, "routes.json"), "w") as f:  # (an older engine's push left one there)
            json.dump({"/primary-socket|%0": {"channel": "1"}}, f)
        mine = tempfile.mkdtemp()
        with open(os.path.join(mine, "routes.json"), "w") as f:
            json.dump({"/standby-socket|%0": {"channel": "2", "epoch": 93}}, f)
        standby.take_in(target, mine)
        with open(os.path.join(mine, "routes.json")) as f:
            self.assertEqual(json.load(f), {"/standby-socket|%0": {"channel": "2", "epoch": 93}}, "this machine's own, kept")

    def test_take_in_and_checks(self):
        target = os.path.join(tempfile.mkdtemp(), "state-standby")
        standby.unpack(standby.bundle(self.state, standby.meta(HERE, "host-a")), target)
        self.assertEqual(standby.checks(target, HERE), (None, None, None))
        mine = tempfile.mkdtemp()
        with open(os.path.join(mine, "heartbeat"), "w") as f:
            f.write("mine")
        self.assertIn("channels.json", standby.take_in(target, mine))
        with open(os.path.join(mine, "heartbeat")) as f:
            self.assertEqual(f.read(), "mine")
        self.assertEqual(standby.checks(tempfile.mkdtemp(), HERE)[0], "why_no_state")
        info = standby.meta(HERE, "host-a")
        info["at"] -= standby.STALE_SECS + 60
        standby.unpack(standby.bundle(self.state, info), target)
        self.assertIsNotNone(standby.checks(target, HERE)[1], "old state: taken over, its time said")

    def test_own_cc_health_kept(self):
        """#code-review on 9d2b7bd: this machine's own entry (a pin to an older Claude Code) survives a takeover."""
        target = os.path.join(tempfile.mkdtemp(), "state-standby")
        with open(os.path.join(self.state, "cc_health.json"), "w") as f:
            json.dump({"machines": {"host-a": {"pin": "2.1.1"}, "host-c": {"broken": {}}, "host-b": {"pin": "9"}}}, f)
        standby.unpack(standby.bundle(self.state, {"at": 1}), target)
        mine = tempfile.mkdtemp()
        with open(os.path.join(mine, "cc_health.json"), "w") as f:
            json.dump({"machines": {"host-b": {"pin": "2.1.300"}}}, f)
        standby.take_in(target, mine, "host-b")
        with open(os.path.join(mine, "cc_health.json")) as f:
            got = json.load(f)["machines"]
        self.assertEqual(got, {"host-a": {"pin": "2.1.1"}, "host-c": {"broken": {}}, "host-b": {"pin": "2.1.300"}})
        other = tempfile.mkdtemp()
        standby.take_in(target, other, "host-b")
        with open(os.path.join(other, "cc_health.json")) as f:
            self.assertNotIn("host-b", json.load(f)["machines"], "no entry of its own: none from the bot either")

    def test_what_is_pushed_when(self):
        a = standby.what_matters({"1": {"session_id": "s", "busy": True, "turns": 3}})
        self.assertEqual(a, standby.what_matters({"1": {"session_id": "s", "busy": False, "turns": 4}}), "busy, turns: no")
        self.assertNotEqual(a, standby.what_matters({"1": {"session_id": "t"}}))
        self.assertFalse(standby.due(60, True), "a change: not before 5 minutes")
        self.assertTrue(standby.due(standby.PUSH_SECS, True))
        self.assertFalse(standby.due(standby.PUSH_SECS * 10, False))
        self.assertTrue(standby.due(standby.FULL_SECS, False), "everything every 6 hours")
        self.assertTrue(standby.due(30, True, placed=True), "an attach, a new conversation: at once (manager 11-09 (c))")
        a = {"1": {"pane": {"id": "%0", "pid": 1}, "busy": True}}
        self.assertEqual(standby.what_placed(a), standby.what_placed({"1": {"pane": {"id": "%0", "pid": 1}, "busy": False}}))
        self.assertNotEqual(standby.what_placed(a), standby.what_placed({"1": {"pane": {"id": "%0", "pid": 2}}}))
        self.assertNotEqual(standby.what_placed(a), standby.what_placed(dict(a, **{"1": dict(a["1"], session_id="n")})))

    def test_dcremote_state_in(self):
        home = tempfile.mkdtemp()
        text = standby.bundle(self.state, {"at": 1})
        r = subprocess.run([sys.executable, os.path.join(HERE, "tools", "dcremote"), "state-in"], input=text, text=True,
                           capture_output=True, env=dict(os.environ, HOME=home), timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout)["files"], 4)
        import shutil
        shutil.rmtree(os.path.join(HERE, "state-standby"), ignore_errors=True)



class Adopt(unittest.TestCase):
    """C-163 4.1: after a takeover, the old bot machine's channels are windows there (never opened here)."""

    def bot(self, states):
        from unittest import mock
        from bot import main
        b = mock.Mock()
        b.channels_state = states
        b.from_primary = lambda st: main.Bot.from_primary(b, st)
        b.adopt_one = lambda st, p, f: main.Bot.adopt_one(b, st, p, f)
        return main, b

    def test_each_kind_of_channel(self):
        found = {"socket": "/t/tmux-9/dcbot", "panes": [{"session": "dc-a", "id": "%3", "pid": 77, "socket": "/t/tmux-9/dcbot"}]}
        main, b = self.bot({})
        live = {"tmux": "dc-a", "session_id": "s1"}
        gone = {"tmux": "dc-b", "session_id": "s2"}
        home = {"home": {"socket": "/u", "session": "w"}, "pane": {"socket": "/u", "id": "%1"}}
        attached = {"pane": {"socket": "/u", "id": "%2"}}
        far = {"home": {"host": "host-c", "socket": "/c", "session": "w"}}
        for st in (live, gone, home, attached):
            self.assertTrue(b.from_primary(st), st)
        self.assertFalse(b.from_primary(far), "another machine's: as it was")
        for st in (live, gone, home, attached):
            b.adopt_one(st, "host-a", found)
        self.assertEqual(live["pane"], {"host": "host-a", "socket": "/t/tmux-9/dcbot", "id": "%3", "pid": 77}, "taken as it is")
        self.assertEqual(live["home"]["host"], "host-a")
        self.assertNotIn("tmux", live)
        self.assertEqual(gone["home"], {"host": "host-a", "socket": "/t/tmux-9/dcbot", "session": "dc-b", "window": ""},
                         "opened again there, never here")
        self.assertNotIn("pane", gone)
        self.assertEqual((home["home"]["host"], home["pane"]["host"], attached["pane"]["host"]), ("host-a",) * 3)
        self.assertEqual(live["took_from"], {"machine": "host-a", "tmux": "dc-a"}, "kept for the failback")
        self.assertFalse(b.from_primary(live), "adopted once")

    def test_own_channels_go_here(self):
        main, b = self.bot({"1": {"home": {"host": "host-b", "socket": "/s", "session": "w"}, "pane": {"host": "host-b", "id": "%1"}},
                            "2": {"home": {"host": "host-c", "socket": "/c"}}, "3": {"tmux": "dc-a"}})
        self.assertEqual(main.Bot.localize_own(b, "host-b"), 1)
        one = b.channels_state["1"]
        self.assertNotIn("host", one["home"])
        self.assertNotIn("host", one["pane"])
        self.assertEqual(one["took_from"], {"local": ["home", "pane"], "machine": "host-b"})
        self.assertEqual(b.channels_state["2"]["home"]["host"], "host-c")
        self.assertNotIn("took_from", b.channels_state["3"], "the old bot machine's: adopt_one's")
        self.assertFalse(b.from_primary(one), "never taken as the old bot machine's")

    def test_out_of_reach_waits(self):
        import asyncio
        from bot import tmux_bridge
        main, b = self.bot({})
        st = {"tmux": "dc-a"}
        b.adopt_one(st, "host-a", None)
        self.assertEqual(st["took_from"], {"machine": "host-a", "pending": True})
        self.assertEqual(st["tmux"], "dc-a", "nothing changed yet")
        bridge = tmux_bridge.TmuxBridge.__new__(tmux_bridge.TmuxBridge)
        with self.assertRaises(tmux_bridge.NotOpened):
            asyncio.run(bridge.ensure(None, st, None))

    def test_the_notice(self):
        import asyncio
        from unittest import mock
        from bot import calls, messages
        main, b = self.bot({})
        b.day_stopped = lambda: main.Bot.day_stopped(b)
        sent = mock.AsyncMock()
        state = tempfile.mkdtemp()
        with mock.patch.object(calls, "system", sent), mock.patch.object(calls, "mention", lambda: "<@42>"), \
                mock.patch.object(main.remote, "this_machine", lambda: "host-b"), mock.patch.object(main, "STATE", state):
            with open(os.path.join(state, "lease-takeovers.json"), "w") as f:
                json.dump([time.time() - 7200, time.time()], f)
            asyncio.run(main.Bot.say_takeover(b, {"primary": "host-a", "epoch": 2}, 3, False))
            asyncio.run(main.Bot.say_takeover(b, {"primary": "host-a", "epoch": 2}, 3, True))
            with open(os.path.join(state, "lease-takeovers.json"), "w") as f:  # the 3rd in 24 hours (drill item 13)
                json.dump([time.time() - 90000, time.time() - 7200, time.time() - 3600, time.time()], f)
            asyncio.run(main.Bot.say_takeover(b, {"primary": "host-a", "epoch": 2}, 3, False))
            asyncio.run(main.Bot.say_takeover(b, {"primary": "host-a", "epoch": 2}, 3, True))
        first, second, third, fourth = (c.args[3] for c in sent.call_args_list)
        self.assertTrue(first.startswith(messages.lease("took_over", primary="host-a", standby="host-b", n=3)))
        self.assertIn("也接上了", second)
        self.assertTrue(first.endswith("<@42>"))
        for got in (first, second):
            self.assertNotIn("第 3 次", got)
            self.assertIn("等 host-a 回來才恢復", got)
        for got in (third, fourth):
            self.assertIn("這是 24 小時內第 3 次換機器：接下來 24 小時內不會再自動切換，host-a 回來也不會自己切回", got)
            self.assertIn("等切回 host-a 才恢復", got)
            self.assertNotIn("等 host-a 回來才恢復", got)
        self.assertIn("也接上了", fourth)


class Waiting(unittest.TestCase):
    """#code-review-2 on 9d2b7bd (manager): from the moment the takeover is read, the old bot machine's channels are
    touched by nothing until they are taken over; the owner's messages to them wait and then go there."""

    def make(self, states):
        import types
        from unittest import mock
        from bot import main
        b = types.SimpleNamespace(channels_state=states, saved=0, closed=False, seen=[])
        for name in ("from_primary", "mark_pending", "adopt_waits", "pending_machine", "localize_own", "adopt_one",
                     "adopt_primary", "adopt_rounds", "adopt_done", "in_background"):
            setattr(b, name, types.MethodType(getattr(main.Bot, name), b))
        b.save = lambda: setattr(b, "saved", b.saved + 1)
        b.is_closed = lambda: b.closed
        b.leader_ok = lambda: True
        b.get_channel = lambda i: types.SimpleNamespace(name="talk", id=i)
        b.say_takeover = mock.AsyncMock()
        b.tmux = types.SimpleNamespace(look_again=mock.AsyncMock())
        b.replay_all = mock.AsyncMock(return_value=0)

        async def on_message(m):
            st = states[str(m.channel.id)]
            b.seen.append((m.content, dict(st.get("pane") or {}), st.get("tmux")))
        b.on_message = on_message
        return main, b

    def msg(self, cid, text):
        import types
        return types.SimpleNamespace(channel=types.SimpleNamespace(id=int(cid)), content=text)

    def test_held_until_taken_over_then_sent_to_the_window_there(self):
        import asyncio
        from unittest import mock
        states = {"11": {"tmux": "dc-talk-0011", "session_id": "s1", "cwd": "/w", "busy": True},
                  "12": {"kind": "files", "files": {"root": "/w"}},
                  "13": {"kind": "conversation", "cwd": "/w"},
                  "14": {"tmux": "dc-p-0014", "session_id": "s4", "cwd": "/w", "mode": "print"},
                  "15": {"home": {"host": "host-c", "socket": "/c", "session": "w"}, "session_id": "s5"}}
        main, b = self.make(states)
        b.mark_pending("host-a")
        self.assertTrue(b.adopting)
        self.assertEqual(sorted(c for c, st in states.items() if (st.get("took_from") or {}).get("pending")),
                         ["11", "12", "13", "14"], "another machine's: as it was")
        self.assertTrue(b.adopt_waits(self.msg("11", "hello")), "held, not opened here")
        self.assertFalse(b.adopt_waits(self.msg("15", "x")), "another machine's channel: as always")
        panes = {"socket": "/t/dcbot", "panes": [{"session": "dc-talk-0011", "id": "%3", "pid": 7, "socket": "/t/dcbot"}]}
        with mock.patch.object(main.remote, "dcremote", mock.AsyncMock(return_value=(0, json.dumps(panes)))), \
                mock.patch.object(main.remote, "this_machine", lambda: "host-b"):
            asyncio.run(b.adopt_primary({"primary": "host-a", "epoch": 2}))
        self.assertEqual(b.seen, [("hello", {"host": "host-a", "socket": "/t/dcbot", "id": "%3", "pid": 7}, None)],
                         "sent after the takeover, to the window on host-a")
        self.assertFalse(b.adopting)
        self.assertEqual(states["12"]["files"]["host"], "host-a", "a files channel browses host-a")
        self.assertEqual(states["13"]["home"], {"host": "host-a", "socket": "/t/dcbot", "session": "dc-talk-13", "window": ""},
                         "never opened yet: on host-a when it is")
        self.assertEqual((states["14"]["mode"], states["14"]["took_from"]["mode"]), ("tmux", "print"))
        self.assertIsNone(b.pending_machine())
        b.say_takeover.assert_awaited_once()
        looked = [c.args[0] for c in b.tmux.look_again.await_args_list]
        self.assertEqual(sorted(looked), ["11", "12", "13", "14"], "busy ones looked at again once taken over (#code-review-2)")

    def test_an_error_leaves_nothing_waiting(self):
        """#code-review-2 on 65ee18f: whatever ends adopt_primary(), the held messages go on and posts again."""
        import asyncio
        from unittest import mock
        states = {"11": {"tmux": "dc-talk-0011", "session_id": "s1", "cwd": "/w"}}
        main, b = self.make(states)
        b.mark_pending("host-a")
        b.adopt_waits(self.msg("11", "hello"))
        with mock.patch.object(main.remote, "dcremote", mock.AsyncMock(side_effect=RuntimeError("boom"))), \
                mock.patch.object(main.remote, "this_machine", lambda: "host-b"):
            with self.assertRaises(RuntimeError):
                asyncio.run(b.adopt_primary({"primary": "host-a", "epoch": 2}))
        self.assertFalse(b.adopting)
        self.assertEqual([m[0] for m in b.seen], ["hello"])

    def test_out_of_reach_the_held_go_on_and_a_restart_tries_again(self):
        import asyncio
        from unittest import mock
        states = {"11": {"tmux": "dc-talk-0011", "session_id": "s1", "cwd": "/w"}}
        main, b = self.make(states)
        b.mark_pending("host-a")
        b.adopt_waits(self.msg("11", "hello"))
        calls = []

        async def nobody(*a, **k):
            calls.append(a)
            b.closed = len(calls) >= 1  # one round, then the bot "stops"
            return 255, ""
        with mock.patch.object(main.remote, "dcremote", nobody), mock.patch.object(main.remote, "this_machine", lambda: "host-b"), \
                mock.patch.object(main.asyncio, "sleep", mock.AsyncMock()):
            asyncio.run(b.adopt_primary({"primary": "host-a", "epoch": 2}))
        self.assertEqual([m[0] for m in b.seen], ["hello"], "after the first try: on, to be told host-a is not reached")
        self.assertEqual(b.pending_machine(), "host-a", "still waiting: a restart picks it up (start_lease)")

    def test_pump_does_not_call_a_waiting_channel_gone(self):
        import asyncio
        from unittest import mock
        from bot import tmux_bridge
        bridge = tmux_bridge.TmuxBridge.__new__(tmux_bridge.TmuxBridge)
        st = {"tmux": "dc-a", "busy": True, "took_from": {"machine": "host-a", "pending": True}}
        bridge.host = mock.Mock(lease=None, channels_state={"11": st})
        for name in ("read_words", "remember_pids", "read_rewriter", "posted_left"):
            setattr(bridge, name, mock.AsyncMock())
        bridge.start_streams, bridge.unsent = mock.Mock(), mock.Mock()
        bridge.say = mock.AsyncMock()
        bridge.pump_once = mock.AsyncMock(side_effect=asyncio.CancelledError)
        with mock.patch.object(tmux_bridge, "write_settings", lambda: None), \
                mock.patch.object(tmux_bridge.tmuxio, "standing", mock.AsyncMock(return_value="gone")) as standing:
            try:
                asyncio.run(bridge.pump())
            except asyncio.CancelledError:
                pass
        standing.assert_not_called()
        bridge.say.assert_not_called()
        self.assertTrue(st["busy"], "not let go of")


class Streams(unittest.TestCase):
    def test_the_old_bot_machine_is_streamed_by_its_own_spool(self):
        from unittest import mock
        from bot import remote
        machines = {"machines": {"host-a": {"roles": ["dcbot"], "ssh": ["a"]}, "host-b": {"roles": ["dcbot-standby"], "ssh": ["b"]},
                                 "host-c": {"roles": ["dcattach"], "ssh": ["c"]}}}
        with mock.patch.object(remote, "inventory", lambda: machines), mock.patch.object(remote, "this_machine", lambda: "host-b"), \
                mock.patch.object(remote, "engine_of", lambda h: "/e"):
            with mock.patch.object(remote, "EPOCH", None):
                self.assertNotIn("host-a", remote.hosts(), "no epoch: as always")
            with mock.patch.object(remote, "EPOCH", 2):
                self.assertIn("host-a", remote.hosts())
                self.assertTrue(remote.stream_command("host-a").endswith("stream --bot-spool"))
                self.assertTrue(remote.stream_command("host-c").endswith(" stream"))

    def test_bot_panes_and_an_epoch_on_the_command_lines_requests(self):
        home = tempfile.mkdtemp()
        tool = os.path.join(HERE, "tools", "dcremote")
        fake = os.path.join(home, "bin")  # (a tmux of its own: the bot's server there is no test's to ask)
        os.makedirs(fake)
        with open(os.path.join(fake, "tmux"), "w") as f:
            f.write("#!/bin/sh\nprintf 'dc-a\\t%%3\\t77\\t/t/dcbot\\nbad line\\n'\n")
        os.chmod(os.path.join(fake, "tmux"), 0o755)
        env = dict(os.environ, HOME=home)
        r = subprocess.run([sys.executable, tool, "bot-panes"], capture_output=True, text=True, timeout=60,
                           env=dict(env, PATH=fake + os.pathsep + os.environ["PATH"]))
        got = json.loads(r.stdout)
        self.assertIn("socket", got)
        self.assertEqual(got["panes"], [{"session": "dc-a", "id": "%3", "pid": 77, "socket": "/t/dcbot"}])
        subprocess.run([sys.executable, tool, "--epoch", "3", "bot-host", "host-b", "3"], env=env, timeout=60)
        r = subprocess.run([sys.executable, tool, "--epoch", "2", "claude-at", "x"], capture_output=True, text=True, env=env,
                           timeout=60)
        self.assertEqual(r.returncode, 75, "a bot's (it says an epoch) is compared (c1's low)")


class FailbackMarks(unittest.TestCase):
    """C-163 drill problem A, manager 11-09 (b): the failback says which channels the standby left as it took them."""
    def test_marks_and_merge(self):
        import tempfile
        from bot import standby
        state, sdir = tempfile.mkdtemp(), tempfile.mkdtemp()
        self.assertEqual(standby.failback_marks({"1": {}}, state), {}, "no base: nothing marked (as before)")
        base = {"1": {"pane": {"id": "%0", "pid": 111}, "busy": False}, "2": {"tmux": "dc-2"}, "3": {"tmux": "dc-3"}}
        with open(os.path.join(sdir, "channels.json"), "w") as f:
            json.dump(base, f)
        standby.keep_base(sdir, state)
        back = {"1": {"pane": {"id": "%0", "pid": 111}, "busy": True, "turns": 4},  # (only what a takeover does not
                "2": {"tmux": "dc-2", "session_id": "new"}, "9": {"home": {"host": "b"}}}  # act on changed: untouched)
        marks = standby.failback_marks(back, state)
        self.assertEqual(marks, {"untouched": ["1"], "gone": ["3"]})
        own = {"1": {"pane": {"id": "%0", "pid": 222}}, "2": {"tmux": "dc-2"}, "3": {"tmux": "dc-3"}, "7": {"tmux": "dc-7"}}
        self.assertEqual(standby.failback_merge(back, own, marks),
                         {"1": {"pane": {"id": "%0", "pid": 222}}, "2": {"tmux": "dc-2", "session_id": "new"},
                          "9": {"home": {"host": "b"}}, "7": {"tmux": "dc-7"}})
        self.assertEqual(standby.failback_merge(back, {}, marks), back, "the primary's own unreadable: as pushed")
        self.assertEqual(standby.failback_merge(back, own, {}), back, "no marks (an older standby): as pushed")
        os.remove(os.path.join(sdir, "channels.json"))
        standby.keep_base(sdir, state)
        self.assertFalse(os.path.exists(os.path.join(state, standby.BASE)), "nothing pushed: no old base kept")


if __name__ == "__main__":
    unittest.main()
