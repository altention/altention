"""Opening a channel's session: ensure(), ensure_home() and wait_ready() (no test at all before 2026-10-11: every
test that went through submit() put an empty function in ensure's place). What is started, with what, what is tried
again, what the owner is told, and what is never touched (an attached pane is the owner's own terminal)."""
import asyncio
import contextlib
import os
import time
import unittest
from unittest import mock

from bot import config, tmux_bridge
from tests import fakepane
from tests.fakepane import box
from tests.test_turn_end import Ch, Host

real_sleep = asyncio.sleep
TRUST = "\n".join(["", " Do you trust the files in this folder?", "", " %s 1. Yes, I trust this folder", " %s 2. No, exit", "",
                   " Enter to confirm · Esc to cancel", ""])


async def nap(seconds, result=None):
    await real_sleep(min(seconds, 0.01))
    return result


class TheHost(Host):
    saves = 0

    def note(self, ch, st):
        return "you are in #%s" % ch.name

    def save(self):
        self.saves += 1


class Tmux(object):
    """The tmux server, as far as opening a session goes. `comes_up(argv)` says what a started Claude does:
    "ready" (it reports SessionStart), "silent" (it runs but never reports), "exits" (gone at once)."""

    def __init__(self, test, comes_up=lambda argv: "ready"):
        self.test, self.comes_up, self.up, self.calls, self.view = test, comes_up, set(), [], box("❯ ")
        self.opens, self.said = True, "tmux said no"

    def key(self, t):
        return t["id"] if isinstance(t, dict) else t

    async def alive(self, t):
        return self.key(t) in self.up

    def begin(self, where, argv):
        how = self.comes_up(argv)
        if how != "exits":
            self.up.add(where)
        if how == "ready":
            ev = {"channel": str(self.test.ch.id), "hook_event_name": "SessionStart", "source": "startup", "session_id": "new-session"}
            asyncio.ensure_future(self.test.bridge.handle(ev))

    async def start(self, name, cwd, argv, env=None):
        self.calls.append(("start", name, cwd, list(argv), dict(env or {})))
        if not self.opens:
            raise OSError("No such file or directory: 'tmux'")
        self.begin(name, argv)
        return True

    async def open_window(self, socket, session, window_name, cwd, argv, env=None, host=None, why=None):
        self.calls.append(("open_window", socket, session, window_name, cwd, list(argv), dict(env or {}), host))
        if not self.opens:
            why.append(self.said)
            return None
        pane = {"socket": socket, "id": "%%%d" % (len(self.calls) + 10), "pid": 4321}
        if host:
            pane["host"] = host
        self.begin(pane["id"], argv)
        return pane

    async def kill(self, t):
        self.calls.append(("kill", t))
        self.up.discard(self.key(t))

    async def kill_pane(self, pane):
        self.calls.append(("kill_pane", pane["id"]))
        self.up.discard(pane["id"])

    async def raw_screen(self, t):
        return self.view(self) if callable(self.view) else self.view

    async def keys(self, t, *names):
        self.calls.append(("keys",) + names)

    def did(self, what):
        return [c for c in self.calls if c[0] == what]


class Base(unittest.TestCase):
    def setUp(self):
        self.ch = Ch()
        self.bridge = tmux_bridge.TmuxBridge(TheHost(self.ch))
        self.st = self.bridge.host.chan(self.ch)
        self.st.update({"cwd": "/w"})
        self.rt = self.bridge.rt(self.ch)

    def go(self, coro, tmux, secs=0.3, env=None):
        async def run():
            try:
                return await coro
            except tmux_bridge.BridgeError as e:
                return e
        with fakepane.use(tmux), mock.patch.object(asyncio, "sleep", nap), mock.patch.object(tmux_bridge, "START_SECS", secs), \
                mock.patch.object(tmux_bridge.routes, "bind_any", mock.AsyncMock()) as self.bound, \
                mock.patch.object(tmux_bridge, "installation", getattr(self, "installation", lambda setting: None)), \
                mock.patch.dict(os.environ, env if env is not None else {"LANG": "en_US.UTF-8"}, clear=env is not None), \
                contextlib.redirect_stderr(open(os.devnull, "w")):
            return asyncio.run(run())

    def said(self):
        return "\n".join(m.content for m in self.ch.sent)


class Ensure(Base):
    """A session the bot keeps in its own tmux server (st["tmux"])."""

    def test_one_that_runs_is_left_as_it_is(self):
        tmux = Tmux(self)
        tmux.up.add("s")
        self.assertIsNone(self.go(self.bridge.ensure(self.ch, self.st, self.rt), tmux))
        self.assertEqual(tmux.calls, [])

    def test_a_new_one_is_started_with_what_reports_back(self):
        tmux = Tmux(self)
        self.st.pop("tmux")
        self.assertIsNone(self.go(self.bridge.ensure(self.ch, self.st, self.rt), tmux, env={}))
        (_, name, cwd, argv, env), = tmux.did("start")
        self.assertEqual((name, cwd), (self.st["tmux"], "/w"))
        self.assertTrue(name.startswith("dc-") and name.endswith(str(self.ch.id)), name)
        self.assertEqual(argv[0], config.CLAUDE)
        self.assertEqual(argv[argv.index("--settings") + 1], tmux_bridge.SETTINGS_FILE, "the hooks that report to the bot")
        self.assertEqual(argv[argv.index("--model") + 1], config.MODEL)
        self.assertEqual(argv[argv.index("--permission-mode") + 1], "bypassPermissions")
        self.assertEqual(argv[argv.index("--append-system-prompt") + 1], "you are in #c")
        self.assertNotIn("--resume", argv)
        self.assertEqual(env, {"DCBOT_ROOT": config.ROOT, "DCBOT_CHANNEL": str(self.ch.id), "LANG": "C.UTF-8"},
                         "started from cron there is no locale: one is given")
        self.assertEqual(self.st["session_id"], "new-session", "what SessionStart reported: the next start resumes it")
        self.assertEqual((self.said(), tmux.did("kill")), ("", []))

    def test_a_locale_that_is_set_is_not_replaced(self):
        for env in ({"LANG": "zh_TW.UTF-8"}, {"LC_ALL": "en_US.UTF-8"}):
            self.setUp()
            tmux = Tmux(self)
            self.go(self.bridge.ensure(self.ch, self.st, self.rt), tmux, env=env)
            self.assertNotIn("LANG", tmux.did("start")[0][4], env)

    def test_the_saved_conversation_is_resumed(self):
        tmux = Tmux(self)
        self.st.update({"session_id": "saved-1", "busy": True, "bg": 2})
        self.assertIsNone(self.go(self.bridge.ensure(self.ch, self.st, self.rt), tmux))
        (_, _, _, argv, _), = tmux.did("start")
        self.assertEqual(argv[-2:], ["--resume", "saved-1"])
        self.assertEqual(self.said(), "")
        self.assertFalse(self.st["busy"], "whatever ran in the session that is gone is not running now")
        self.assertEqual(self.st["bg"], 2, "resumed: its background work is still its own to report")

    def test_a_conversation_that_cannot_be_resumed_gives_way_to_a_new_one_and_says_so(self):
        tmux = Tmux(self, lambda argv: "exits" if "--resume" in argv else "ready")
        self.st.update({"session_id": "saved-1", "bg": 2})
        self.assertIsNone(self.go(self.bridge.ensure(self.ch, self.st, self.rt), tmux))
        first, second = tmux.did("start")
        self.assertIn("--resume", first[3])
        self.assertNotIn("--resume", second[3])
        self.assertEqual(len(tmux.did("kill")), 1, "what was left of the first try is cleared away")
        self.assertEqual(self.said(), "接不回原本的對話（`saved-1`），已開始新的對話。")
        self.assertNotIn("bg", self.st)

    def test_one_that_never_reports_is_given_up_showing_its_screen(self):
        tmux = Tmux(self, lambda argv: "silent")
        tmux.view = "\n".join(["", " API Error: 401", " Please run /login", ""])
        self.st["session_id"] = "saved-1"
        err = self.go(self.bridge.ensure(self.ch, self.st, self.rt), tmux, secs=0.15)
        self.assertIsInstance(err, tmux_bridge.BridgeError)
        self.assertIn("Claude 沒有啟動成功", str(err))
        self.assertIn("Please run /login", str(err), "the last screen: why it did not come up")
        self.assertEqual((len(tmux.did("start")), len(tmux.did("kill"))), (2, 2), "with the conversation, then without; nothing left behind")
        self.assertEqual(tmux.up, set())

    def test_one_that_exits_at_once(self):
        tmux = Tmux(self, lambda argv: "exits")
        err = self.go(self.bridge.ensure(self.ch, self.st, self.rt), tmux)
        self.assertEqual(str(err), "Claude 沒有啟動成功。")

    def test_tmux_itself_missing(self):
        tmux = Tmux(self)
        tmux.opens = False
        err = self.go(self.bridge.ensure(self.ch, self.st, self.rt), tmux)
        self.assertIn("無法執行 tmux", str(err))

    def test_submit_says_why_and_types_nothing(self):
        tmux = Tmux(self, lambda argv: "exits")

        class Message(object):
            channel, id, content = self.ch, 1, "hello"
        self.go(self.bridge.submit(Message(), "hello"), tmux)
        self.assertEqual(self.said(), "出錯了：Claude 沒有啟動成功。\n-# 處理好之後按下面的「再送一次」", "C-74: kept, with the button")
        self.assertEqual([e["text"] for e in self.st["unsent"].values()], ["hello"])
        self.assertEqual(tmux.did("keys"), [])
        self.assertGreaterEqual(self.bridge.host.saves, 1)

    def test_start_now_says_whether_it_came_up(self):
        self.assertTrue(self.go(self.bridge.start_now(self.ch), Tmux(self)))
        self.assertGreater(self.st["last_active"], time.time() - 5, "or the idle reaper takes a session nobody has talked to yet")
        self.setUp()
        self.assertFalse(self.go(self.bridge.start_now(self.ch), Tmux(self, lambda argv: "exits")))
        self.assertEqual(self.said(), "出錯了：Claude 沒有啟動成功。")


class AttachedPane(Base):
    """The owner's own terminal (st["pane"], no st["home"]): nothing is ever started, restarted or closed there."""

    def setUp(self):
        Base.setUp(self)
        self.st.pop("tmux")
        self.st["pane"] = {"socket": "/tmp/tmux-1/default", "id": "%7"}

    def test_running_claude_code(self):
        tmux = Tmux(self)
        tmux.up.add("%7")
        self.assertIsNone(self.go(self.bridge.ensure(self.ch, self.st, self.rt), tmux))
        self.assertEqual(tmux.calls, [])

    def test_no_longer_running_claude_code(self):
        tmux = Tmux(self)
        self.st["session_id"] = "saved-1"
        err = self.go(self.bridge.ensure(self.ch, self.st, self.rt), tmux)
        self.assertIn("已經沒有在跑 Claude Code", str(err))
        self.assertIn("!detach", str(err))
        self.assertIn("%7", str(err))
        self.assertEqual(tmux.calls, [], "nothing started, nothing killed")


class Home(Base):
    """A window the bot opens in the owner's own tmux (st["home"]): only such a window is ever reopened or closed."""

    def setUp(self):
        Base.setUp(self)
        self.st.pop("tmux")
        self.st["home"] = {"socket": "/tmp/tmux-1/default", "session": "work", "window": "notes"}

    def test_its_window_still_there(self):
        tmux = Tmux(self)
        tmux.up.add("%7")
        self.st["pane"] = {"socket": "/tmp/tmux-1/default", "id": "%7"}
        self.assertIsNone(self.go(self.bridge.ensure(self.ch, self.st, self.rt), tmux))
        self.assertEqual(tmux.calls, [])

    def test_a_window_is_opened_and_remembered(self):
        tmux = Tmux(self)
        self.st["session_id"] = "saved-1"
        self.assertIsNone(self.go(self.bridge.ensure(self.ch, self.st, self.rt), tmux))
        (_, socket, session, window, cwd, argv, env, host), = tmux.did("open_window")
        self.assertEqual((socket, session, window, cwd, host), ("/tmp/tmux-1/default", "work", "notes", "/w", None))
        self.assertEqual((argv[0], argv[-2:]), (config.CLAUDE, ["--resume", "saved-1"]))
        self.assertIn("--settings", argv)
        self.assertEqual((env["DCBOT_ROOT"], env["DCBOT_CHANNEL"]), (config.ROOT, str(self.ch.id)))
        self.assertEqual(self.st["pane"]["id"], tmux.calls[0] and self.st["pane"]["id"])
        self.assertIn(self.st["pane"]["id"], tmux.up)
        self.assertGreaterEqual(self.bridge.host.saves, 1, "the pane is kept with the channel")
        self.bound.assert_not_called()
        self.assertEqual(self.said(), "")

    def test_a_dead_window_is_opened_again(self):
        tmux = Tmux(self)
        self.st["pane"] = {"socket": "/tmp/tmux-1/default", "id": "%7"}  # closed by the owner
        self.assertIsNone(self.go(self.bridge.ensure(self.ch, self.st, self.rt), tmux))
        self.assertEqual(len(tmux.did("open_window")), 1)
        self.assertNotEqual(self.st["pane"]["id"], "%7")

    def test_on_another_machine_its_own_claude_and_its_route(self):
        tmux = Tmux(self, lambda argv: "silent")  # its SessionStart may come before the route is written
        self.st["home"]["host"] = "lap"
        self.assertIsNone(self.go(self.bridge.ensure(self.ch, self.st, self.rt), tmux))
        (_, _, _, _, _, argv, env, host), = tmux.did("open_window")
        self.assertEqual((host, argv[0]), ("lap", "claude"), "that machine's own claude")
        self.assertNotIn("--settings", argv, "its global hook reports, through its route table")
        self.assertEqual(env, {"DCBOT_CHANNEL": str(self.ch.id)}, "dcpost's sender only: this machine's paths mean nothing there")
        self.bound.assert_awaited_once()
        self.assertEqual(self.bound.await_args[0][1], str(self.ch.id))
        self.assertEqual(self.st["pane"]["host"], "lap", "taken as up once its input box shows")

    def test_a_conversation_that_cannot_be_resumed(self):
        tmux = Tmux(self, lambda argv: "exits" if "--resume" in argv else "ready")
        self.st["session_id"] = "saved-1"
        self.assertIsNone(self.go(self.bridge.ensure(self.ch, self.st, self.rt), tmux))
        self.assertEqual((len(tmux.did("open_window")), len(tmux.did("kill_pane"))), (2, 1))
        self.assertEqual(tmux.did("kill_pane")[0][1], "%11", "the window of the first try, which the bot opened itself")
        self.assertEqual(self.said(), "接不回原本的對話（`saved-1`），已開始新的對話。")

    def test_no_window_can_be_opened(self):
        tmux = Tmux(self)
        tmux.opens = False
        err = self.go(self.bridge.ensure(self.ch, self.st, self.rt), tmux)
        self.assertEqual(str(err), "在 tmux `work` 裡開不起 Claude。\n-# tmux said no")
        self.assertNotIn("pane", self.st)


class WaitReady(Base):
    def wait(self, tmux, name="s", secs=0.3, ready_after=None):
        async def go():
            if ready_after is not None:
                async def later():
                    await real_sleep(ready_after)
                    self.rt.ready.set()
                asyncio.ensure_future(later())
            return await self.bridge.wait_ready(name, self.rt)
        return self.go(go(), tmux, secs=secs)

    def test_the_trust_dialog_is_answered_yes(self):
        tmux = Tmux(self)
        tmux.up.add("s")
        tmux.view = TRUST % ("❯", " ")
        self.assertTrue(self.wait(tmux, ready_after=0.05))
        self.assertEqual(set(tmux.did("keys")), {("keys", "Enter")}, "Yes is the one picked: Enter")

    def test_with_no_picked_it_moves_first(self):
        tmux = Tmux(self)
        tmux.up.add("s")
        tmux.view = lambda t: TRUST % ((" ", "❯") if not t.did("keys") else ("❯", " "))
        self.assertTrue(self.wait(tmux, ready_after=0.1))
        pressed = [c[1] for c in tmux.did("keys")]
        self.assertEqual(pressed[:2], ["Down", "Enter"], "never Enter while No is the one picked")

    def test_no_dialog_no_key(self):
        tmux = Tmux(self)
        tmux.up.add("s")
        self.assertTrue(self.wait(tmux, ready_after=0.05))
        self.assertEqual(tmux.did("keys"), [])

    def test_gone_before_it_reported(self):
        self.assertFalse(self.wait(Tmux(self)))

    def test_never_reporting_ends_after_the_time_allowed(self):
        tmux = Tmux(self)
        tmux.up.add("s")
        t0 = time.time()
        self.assertFalse(self.wait(tmux, secs=0.15))
        self.assertLess(time.time() - t0, 3)

    def test_only_another_machines_pane_is_taken_as_up_by_its_input_box(self):
        tmux = Tmux(self)
        tmux.up.update(["%7", "s"])
        self.assertTrue(self.wait(tmux, name={"socket": "/s", "id": "%7", "host": "lap"}, secs=0.15))
        self.assertFalse(self.wait(tmux, name={"socket": "/s", "id": "%7"}, secs=0.15), "this machine: SessionStart is waited for")
        self.assertFalse(self.wait(tmux, name="s", secs=0.15))


if __name__ == "__main__":
    unittest.main()
