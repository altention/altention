"""C-162 step 2: what counts as Claude Code broken, the probe, stepping down and back (design sections 2.0-2.3)."""
import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))))
from altention import testing as _testing  # noqa: E402
_testing.guard_home()  # tests never touch the real home (altention/testing.py; 11-08: one left a real dcremote-owner)
import asyncio
import contextlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from bot import calls, cc_health, cc_probe, cc_watch, messages
from tests import fakepane
from tests import tmuxguard  # noqa: F401  (no test reaches a real tmux server)

DCBOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class Health(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        for p in (mock.patch.object(cc_health, "path", lambda: os.path.join(self.tmp, "cc_health.json")),
                  mock.patch.object(cc_health, "machine", lambda: "host-a"),
                  mock.patch.object(cc_health, "busy_machine", lambda: None)):
            p.start()
            self.addCleanup(p.stop)


class Counting(Health):
    def test_a_version_that_never_worked_is_suspect_at_once(self):
        self.assertEqual(cc_health.note_fail("tmux", "9.9.9", 1), "suspect")

    def test_three_failures_once_it_worked(self):
        cc_health.note_ok("9.9.9", 1)
        self.assertIsNone(cc_health.note_fail("tmux", "9.9.9", 1))
        self.assertIsNone(cc_health.note_fail("tmux", "9.9.9", 2))
        self.assertEqual(cc_health.note_fail("tmux", "9.9.9", 3), "suspect")

    def test_a_channels_success_ends_its_run(self):
        cc_health.note_ok("9.9.9", 1)
        cc_health.note_fail("tmux", "9.9.9", 1)
        cc_health.note_fail("tmux", "9.9.9", 1)
        cc_health.note_ok("9.9.9", 1)
        self.assertIsNone(cc_health.note_fail("tmux", "9.9.9", 1))

    def test_resume_failures_in_two_channels(self):
        cc_health.note_ok("9.9.9", 1)
        self.assertIsNone(cc_health.note_fail("resume", "9.9.9", 1))
        self.assertIsNone(cc_health.note_fail("resume", "9.9.9", 1))
        self.assertEqual(cc_health.note_fail("resume", "9.9.9", 2), "suspect")

    def test_print_turns_with_no_result(self):
        self.assertIsNone(cc_health.note_fail("json", "9.9.9", 1))
        self.assertIsNone(cc_health.note_fail("json", "9.9.9", 2))
        self.assertEqual(cc_health.note_fail("json", "9.9.9", 1), "json")

    def test_per_version(self):
        cc_health.note_ok("9.9.8", 1)
        cc_health.note_fail("tmux", "9.9.8", 1)
        cc_health.note_fail("tmux", "9.9.8", 1)
        cc_health.note_ok("9.9.9", 1)
        self.assertIsNone(cc_health.note_fail("tmux", "9.9.9", 1), "a new version counts from nothing")

    def test_only_where_errors_are_written(self):
        """#code-review-2 on e30eba7: the conversation above, a token count, a version number are none of it."""
        screen = "\n".join(["❯ 我們之前說 /login 和 usage limit 的事", "● 好的，network 401 那段我看過了",
                             "✻ Thinking… (12s · ↓ 529 tokens)", "Update available: 2.1.401", "line 403 of main.py",
                             "", "", "w4013ab", "some screen", "the bot", "does not", "know", "at all"])
        self.assertIsNone(cc_health.not_the_version(screen))
        self.assertTrue(cc_health.not_the_version("…\nAPI Error: 529 {\"type\":\"overloaded_error\"}"))
        self.assertTrue(cc_health.not_the_version("…\nAPI Error (429) rate limit"))

    def test_not_the_version(self):
        for text in ("Usage limit reached · limit resets 3pm", "Please run /login", "API Error: Connection error.",
                     "getaddrinfo ENOTFOUND api.anthropic.com", "Invalid API key"):
            self.assertTrue(cc_health.not_the_version(text), text)
        self.assertTrue(cc_health.not_the_version("", signal="9"), "the OOM killer")
        self.assertIsNone(cc_health.not_the_version("╭─ some screen the bot does not know ─╮"))
        with mock.patch.object(cc_health, "busy_machine", lambda: "load 99"):
            self.assertEqual(cc_health.not_the_version("plain"), "load 99")

    def test_version_of(self):
        for name, want in (("2.1.319", "2.1.319"), ("claude", None), ("2.1.319.tmp.5", "2.1.319.tmp.5")):
            path = os.path.join(self.tmp, name)
            open(path, "w").close()
            self.assertEqual(cc_health.version_of(path), want, name)
        link = os.path.join(self.tmp, "claude-link")
        os.symlink(os.path.join(self.tmp, "2.1.319"), link)
        self.assertEqual(cc_health.version_of(link), "2.1.319", "links followed")


class Probe(object):
    """A probe whose results are given in turn."""
    def __init__(self, *results):
        self.results, self.runs = list(results), 0

    async def run(self, binary):
        self.runs += 1
        return dict(self.results.pop(0))

    async def print_check(self, binary):
        self.runs += 1
        return dict(self.results.pop(0))


class Bridge(object):
    def __init__(self):
        self.host = mock.Mock()
        self.host.audit = mock.AsyncMock()
        self.reopened = []

    def reopen_off(self, version):
        self.reopened.append(version)

    def held_channels(self):
        return ["1", "2"]

    async def notify_channel(self):
        return None


class WatchBase(Health):
    def setUp(self):
        super().setUp()
        for p in (mock.patch.object(cc_watch, "ACTIVE", True), mock.patch.object(cc_watch, "RETRY_SECS", 0),
                  mock.patch.object(cc_health, "binary", lambda data=None: "/v/9.9.9"),
                  mock.patch.object(cc_health, "version_of", lambda path: "9.9.9")):
            p.start()
            self.addCleanup(p.stop)
        self.drill = mock.AsyncMock(return_value={"ok": True, "started": True, "screen": True, "print": True})
        p = mock.patch.object(cc_watch, "drill", self.drill)  # (never the real tools/cc-drill in a test)
        p.start()
        self.addCleanup(p.stop)
        self.system = mock.AsyncMock(return_value=True)
        self.over = mock.AsyncMock(return_value=True)
        for p in (mock.patch.object(calls, "system", self.system), mock.patch.object(calls, "over", self.over),
                  mock.patch.object(calls, "mention", lambda: "<@42>")):
            p.start()
            self.addCleanup(p.stop)

    def watch(self, *results, started=1):
        return cc_watch.Watch(Bridge(), probe=Probe(*results), started=started)

    def settle(self, w, coro):
        async def go():
            got = await coro
            for _ in range(50):  # what it spawned, to the end (some of it in a thread: off_loop)
                others = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
                if not others:
                    break
                await asyncio.wait(others, timeout=2)
            return got
        return asyncio.run(go())



class Watching(WatchBase):
    def test_inert_unless_started(self):
        with mock.patch.object(cc_watch, "ACTIVE", False):
            w = self.watch()
            self.assertIsNone(asyncio.run(w.failed("tmux", 1)))
        self.assertEqual(cc_health.load(), {})

    def test_not_the_versions_failure_is_not_counted(self):
        w = self.watch()
        self.assertEqual(asyncio.run(w.failed("tmux", 1, text="Please run /login")), "not_the_version")
        self.assertEqual(cc_health.load(), {})

    def test_held_during_the_grace(self):
        import time
        w = self.watch({"ok": True}, started=time.time())
        self.assertEqual(asyncio.run(w.failed("tmux", 1, why="x")), "held")
        w.started = 0
        asyncio.run(w.after_grace())
        self.assertEqual(w.probe.runs, 1, "one probe for what failed in the grace")

    def test_a_clean_probe_forgets(self):
        w = self.watch({"ok": True})
        self.assertEqual(self.settle(w, w.failed("tmux", 1, why="x")), "suspect")
        self.assertEqual(cc_health.level(), 1)
        self.assertNotIn("tmux", cc_health.entry()["versions"]["9.9.9"])
        self.system.assert_not_called()

    def test_two_failures_alike_bring_it_down(self):
        bad = {"ok": False, "step": 3, "why": "回覆之後畫面上找不到輸入框", "screen": "?"}
        w = self.watch(bad, bad)
        self.settle(w, w.failed("tmux", 1, why="x"))
        self.assertEqual(w.probe.runs, 2)
        self.assertEqual(cc_health.level(), 2)
        head = self.system.call_args.args[3]
        self.assertTrue(head.startswith(messages.cc("down_print", version="9.9.9")), head)
        self.assertTrue(head.endswith("<@42>"), "the purpose first, the mention last (C-103)")
        self.assertIn(messages.cc("missing"), self.system.call_args.args[4])

    def test_two_failures_unlike_do_not(self):
        w = self.watch({"ok": False, "step": 1, "why": "Claude Code 一開就結束了（結束碼 1）"},
                       {"ok": False, "step": 3, "why": "回覆之後畫面上找不到輸入框"})
        self.settle(w, w.failed("tmux", 1, why="x"))
        self.assertEqual(cc_health.level(), 1)

    def test_a_probe_that_shows_the_login_does_not(self):
        bad = {"ok": False, "step": 2, "why": "送出的話沒有得到回覆", "screen": "Please run /login"}
        w = self.watch(bad, bad)
        self.settle(w, w.failed("tmux", 1, why="x"))
        self.assertEqual(cc_health.level(), 1)

    def test_stream_json_broken_alone(self):
        """manager 11-07 (b): print channels go to text; tmux channels, whose tmux works, stay as they are."""
        w = self.watch({"ok": False, "why": "沒有拿到結果"})
        for _ in range(3):
            self.settle(w, w.failed("json", 1, why="no result"))
        self.assertEqual(list(cc_health.broken()), ["json"])
        self.assertEqual(cc_health.effective("tmux", {"tmux": "dc-x"}), "tmux", "tmux channels untouched")
        self.assertEqual(cc_health.effective("print", {"tmux": "dc-x"}), "text")
        self.assertEqual(self.system.call_args.args[3], messages.cc("down_json", version="9.9.9"), "no mention (C-103)")

    def test_stream_json_checked_first(self):
        w = self.watch({"ok": True})
        for _ in range(3):
            self.settle(w, w.failed("json", 1, why="no result"))
        self.assertEqual(cc_health.broken(), {}, "the check passed: nothing broken")

    def test_both_broken_runs_tmux_channels_in_text(self):
        bad = {"ok": False, "step": 3, "why": "回覆之後畫面上找不到輸入框", "screen": "?"}
        w = self.watch(bad, bad)
        self.settle(w, w.failed("tmux", 1, why="x"))
        w.probe = Probe({"ok": False, "why": "沒有拿到結果"})
        for _ in range(3):
            self.settle(w, w.failed("json", 1, why="no result"))
        self.assertEqual(cc_health.effective("tmux", {"tmux": "dc-x"}), "text")
        self.assertTrue(self.system.call_args.args[3].startswith(messages.cc("down_text", version="9.9.9")))

    def test_retry(self):
        w = self.watch({"ok": True})
        self.assertEqual(asyncio.run(w.retry()), messages.cc("retry_free"))
        cc_health.set_level(2, version="9.9.8")
        told = []

        async def say(text):
            told.append(text)
        said = asyncio.run(w.retry(say=say))
        self.assertEqual(told, [messages.cc("retry_trying")], "said first: it takes minutes")
        self.assertEqual(said, messages.cc("retry_held") + messages.cc("retry_held_after", mode=messages.cc("mode_print")))
        self.assertEqual(cc_health.level(), 1)
        self.over.assert_awaited_once()
        cc_health.set_level(2, version="9.9.8")
        w.probe = Probe({"ok": False, "step": 1, "why": "畫面上一直找不到輸入框"})
        said = asyncio.run(w.retry())
        self.assertIn("畫面上一直找不到輸入框", said)
        self.assertEqual(cc_health.level(), 2)

    def test_words_when_only_stream_json_broke(self):
        """The screen is fine then: the words name the simple mode, not the screen (#code-review-2 on e36e823)."""
        cc_health.set_broken("json", "9.9.9")
        self.assertTrue(cc_health.json_alone())
        w = self.watch({"ok": False, "why": "沒有拿到結果"})
        said = asyncio.run(w.retry())
        self.assertEqual(said, messages.cc("retry_failed_json", version="9.9.9", why="沒有拿到結果",
                                           mode=messages.cc("mode_tmux")))
        self.assertIn("簡易模式不能用", messages.cc("status_json", version="9.9.9"))
        self.assertIn("簡易模式不能用", messages.cc("mode_held_json", version="9.9.9"))
        cc_health.set_broken("tmux", "9.9.9")
        self.assertFalse(cc_health.json_alone())

    def test_back_on_another_version(self):
        cc_health.set_level(2, version="9.9.8")
        w = self.watch({"ok": False, "step": 1, "why": "x"})
        self.settle(w, w.maybe_back())
        w.version_seen = (0, None)
        self.settle(w, w.maybe_back())
        self.assertEqual(w.probe.runs, 1, "a version that failed lately is not probed again for 30 minutes")
        cc_health.save({})  # (half an hour on)
        cc_health.set_level(2, version="9.9.8")
        w = self.watch({"ok": True})
        self.settle(w, w.maybe_back())
        self.assertEqual(cc_health.level(), 1)
        cc_health.set_level(2, version="9.9.9")
        w = self.watch({"ok": True})
        self.settle(w, w.maybe_back())
        self.assertEqual((cc_health.level(), w.probe.runs), (2, 0), "the same version is not tried again by itself")


class FakeTmux(object):
    """The probe's pane: what it shows step by step."""
    PROMPT = fakepane.box("❯ ")

    def __init__(self, probe, after_reply=None, dead=False, remembers=True):
        self.probe, self.after_reply, self.dead, self.remembers = probe, after_reply, dead, remembers
        self.typed, self.replied, self.opened = [], 0, []

    async def start(self, name, cwd, argv, env=None):
        self.opened.append((argv, env))
        return True

    async def kill(self, t):
        return None

    async def ended(self, name):
        return {"status": "1", "signal": "", "screen": "boom"} if self.dead else None

    async def raw_screen(self, t):
        if self.replied and self.after_reply is not None and len(self.opened) == 1:
            return self.after_reply
        return self.PROMPT

    async def keys(self, t, *names):
        if "Enter" in names:
            self.replied += 1
            word = self.typed[-1].split("：")[-1] if "記住這個字" in self.typed[-1] else ""
            said = "ok" if word else (self.word if self.remembers else "?")
            self.word = getattr(self, "word", None) or word
            self.probe.event({"session_id": "s-probe", "hook_event_name": "SessionStart"})
            self.probe.event({"hook_event_name": "Stop", "last_assistant_message": said,
                              "transcript_path": "/nowhere/s-probe.jsonl"})

    async def type_text(self, t, text):
        self.typed.append(text)


class Probing(Health):
    def probe_with(self, settings="/settings.json", **kw):
        probe = cc_probe.Probe(settings, lambda st: "auto")
        fake = FakeTmux(probe, **kw)
        with mock.patch.object(cc_probe, "folder", lambda: os.path.join(self.tmp, "cc-probe")), \
                mock.patch.object(cc_probe, "START_SECS", 2), contextlib.ExitStack() as stack:
            for name in ("start", "kill", "ended", "raw_screen", "keys", "type_text"):
                stack.enter_context(fakepane.replace(name, getattr(fake, name)))
            got = asyncio.run(probe.run("/v/9.9.9"))
        return got, fake

    def test_all_four_steps(self):
        got, fake = self.probe_with()
        self.assertEqual(got, {"ok": True})
        argv, env = fake.opened[0]
        self.assertEqual(argv[0], "/v/9.9.9")
        self.assertIn("haiku", argv)
        self.assertEqual((env["DCBOT_CHANNEL"], env["ALT_PROBE"], env["ALTENTION_BACKGROUND"]), (cc_probe.PROBE, "1", "1"))
        self.assertIn("--resume", fake.opened[1][0], "step 4 resumes the probe's own conversation")
        self.assertTrue(all("[altention 探測，可忽略]" in t for t in fake.typed), "said so to whoever reads the history")
        self.assertNotIn("probe_session", cc_health.entry(), "closed and forgotten")

    def test_opened_with_a_folder_handed_as_a_channels_attachments_folder_is(self):
        """In its settings, never --add-dir (2.1.326 asks about that one): a question about it fails the probe, as
        it would stop a channel. The settings: a copy of the bot's file with the folder in it, the folder made."""
        from altention.connectors.claude_code import launch
        base = os.path.join(self.tmp, "claude-settings.json")
        launch.write_settings(base, ["Read(z)"], "'/z/dchook'")
        got, fake = self.probe_with(settings=base)
        self.assertEqual(got, {"ok": True})
        folder = os.path.join(self.tmp, "cc-probe", "attachments")
        for argv, _env in fake.opened:
            self.assertNotIn("--add-dir", argv)
            with open(argv[argv.index("--settings") + 1]) as f:
                given = json.load(f)
            self.assertEqual(given["permissions"], {"deny": ["Read(z)"], "additionalDirectories": [folder]})
            self.assertIn("hooks", given)
        self.assertTrue(os.path.isdir(folder), "made before Claude Code starts: a folder not there is dropped")

    def test_box_gone_after_the_reply(self):
        got, _ = self.probe_with(after_reply="some screen with no box")
        self.assertEqual((got["ok"], got["step"]), (False, 3))

    def test_ends_at_once(self):
        got, _ = self.probe_with(dead=True)
        self.assertEqual((got["ok"], got["step"]), (False, 1))
        self.assertIn("一開就結束", got["why"])

    def test_resume_forgets(self):
        got, _ = self.probe_with(remembers=False)
        self.assertEqual((got["ok"], got["step"]), (False, 4))

    def test_no_permission_mode_runs_nothing(self):
        def unset(st):
            raise ValueError("no mode")
        probe = cc_probe.Probe("/settings.json", unset)
        self.assertEqual(asyncio.run(probe.run("/v/9.9.9"))["ok"], None)


class Wiring(Health):
    """Where the bridge and the bot tell the watch, and what they do while held down."""

    def bridge(self, st):
        from bot import tmux_bridge
        b = tmux_bridge.TmuxBridge.__new__(tmux_bridge.TmuxBridge)
        b.host = mock.Mock()
        b.host.chan.return_value = st
        b.cc = mock.Mock()
        b.cc.failed = mock.AsyncMock()
        b.say = mock.AsyncMock()
        return b

    def unknown(self, st, view):
        from bot import tmux_bridge
        b = self.bridge(st)
        r = tmux_bridge.Typing()
        r.refuse("claude_code.dialog")
        with mock.patch.object(cc_watch, "ACTIVE", True), fakepane.replace("raw_screen", mock.AsyncMock(return_value=view)):
            asyncio.run(b.box_unknown(mock.Mock(id=7), st, "dc-x", r))
        return b

    def test_a_screen_not_known_is_counted(self):
        b = self.unknown({"tmux": "dc-x", "cc_version": "9.9.9"}, "nothing the bot knows here")
        b.cc.failed.assert_awaited_once()
        self.assertEqual(b.cc.failed.call_args.args[:3], ("tmux", 7, "9.9.9"))

    def test_a_menu_is_not(self):
        menu = "\n".join(["Do you want to proceed?", "❯ 1. Yes", "  2. No", "", "Esc to cancel"])
        b = self.unknown({"tmux": "dc-x"}, menu)
        b.cc.failed.assert_not_called()

    def test_an_attached_pane_is_told_not_counted(self):
        st = {"pane": {"socket": "/s", "id": "%3"}}
        b = self.unknown(st, "nothing the bot knows here")
        b.cc.failed.assert_not_called()
        said = b.say.call_args.args[1]
        self.assertIn("`!mode print`", said)
        self.assertNotIn("  ", said)
        b2 = self.unknown(st, "nothing the bot knows here")
        b2.say.assert_not_called()  # (said at most every PANE_SAID_SECS)

    def test_start_now_opens_nothing_while_held(self):
        from bot import tmux_bridge
        b = self.bridge({"tmux": "dc-x"})
        b.host.mode = lambda st: "print"
        b.rt = mock.Mock()
        b.ensure = mock.AsyncMock()
        self.assertTrue(asyncio.run(tmux_bridge.TmuxBridge.start_now(b, mock.Mock(id=7))))
        b.ensure.assert_not_called()

    def test_a_queued_turn_says_so_once_when_held(self):
        from bot import main, config
        bot = mock.Mock()
        bot.locks, bot.sem_waiting = {}, 0
        bot.chan.return_value = {"tmux": "dc-x"}
        bot.mode = lambda st: "print"
        bot.held = lambda st: "print"
        bot.leave_window = mock.AsyncMock()
        ch = mock.Mock(id=7)
        ch.send = mock.AsyncMock()
        msg = mock.Mock(channel=ch)
        msg.add_reaction = mock.AsyncMock()

        async def go():
            bot.sem = asyncio.Semaphore(1)
            await bot.sem.acquire()  # every place taken
            gate = asyncio.Event()

            async def turn(m, t):
                gate.set()
            bot.turn = turn
            task = asyncio.ensure_future(main.Bot.print_turn(bot, msg, "hi"))
            for _ in range(5):
                await asyncio.sleep(0)
            bot.sem.release()
            await asyncio.wait_for(task, 2)
        asyncio.run(go())
        ch.send.assert_awaited_once_with(messages.cc("queued", mode=messages.cc("mode_print"), most=config.MAX_PARALLEL, n=1))

    def test_attached_may_switch(self):
        from bot import main
        bot = mock.Mock()
        st = {"pane": {"socket": "/s", "id": "%3"}}
        for how, arg, want in (("gone", "print", True), ("alive", "print", False), ("unknown", "print", False)):
            with fakepane.replace("standing", mock.AsyncMock(return_value=how)) if False else \
                    mock.patch.object(main.tmuxio, "standing", mock.AsyncMock(return_value=how)):
                self.assertEqual(asyncio.run(main.Bot.attached_may_switch(bot, st, "!mode", arg)), want, how)
        self.assertFalse(asyncio.run(main.Bot.attached_may_switch(bot, st, "!mode", "tmux")))
        self.assertTrue(asyncio.run(main.Bot.attached_may_switch(bot, dict(st, mode="print"), "!mode", "tmux")))
        self.assertFalse(asyncio.run(main.Bot.attached_may_switch(bot, st, "!reset", "")))
        far = {"pane": {"host": "host-b", "socket": "/s", "id": "%3"}}
        self.assertFalse(asyncio.run(main.Bot.attached_may_switch(bot, far, "!mode", "print")))


class Pinning(WatchBase):
    """Section 3: the kept copy of the last version that passed !cctest, the pin, the way back, the reminder."""

    def setUp(self):
        super().setUp()
        self.state = os.path.join(self.tmp, "state")
        os.makedirs(self.state)
        p = mock.patch.object(cc_health.config, "STATE", self.state)
        p.start()
        self.addCleanup(p.stop)
        self.old = self.claude("9.9.8", b"old claude")

    def claude(self, version, body):
        path = os.path.join(self.tmp, version)
        with open(path, "wb") as f:
            f.write(body)
        return path

    def test_keep_copies_and_checks(self):
        self.assertIsNone(cc_health.keep("9.9.8", self.old))
        good = cc_health.last_good(check=True)
        self.assertEqual(good["version"], "9.9.8")
        self.assertEqual(good["path"], os.path.join(self.state, "claude-keep", "9.9.8"))
        self.assertEqual(good["sha256"], cc_health.sha256(self.old))
        new = self.claude("9.9.9", b"new claude")
        self.assertIsNone(cc_health.keep("9.9.9", new))
        self.assertEqual(os.listdir(os.path.join(self.state, "claude-keep")), ["9.9.9"], "one copy only")
        with open(cc_health.last_good()["path"], "ab") as f:
            f.write(b"!")
        self.assertIsNone(cc_health.last_good(), "a copy of another size is none")

    def test_no_copy_on_a_full_disk(self):
        with mock.patch.object(cc_health.shutil, "disk_usage", lambda p: mock.Mock(free=10)):
            self.assertIn("1 GB", cc_health.keep("9.9.8", self.old))
        self.assertIsNone(cc_health.last_good())

    def test_falls_back_to_the_kept_version_first(self):
        cc_health.keep("9.9.8", self.old)
        bad = {"ok": False, "step": 3, "why": "回覆之後畫面上找不到輸入框", "screen": "?"}
        w = self.watch(bad, bad, {"ok": True})  # the new version twice, then the kept one
        self.settle(w, w.failed("tmux", 1, why="x"))
        self.assertEqual(cc_health.pin()["version"], "9.9.8")
        self.assertEqual(cc_health.broken(), {}, "pinned: the channels stay in tmux")
        self.assertEqual(w.bridge.reopened, ["9.9.8"], "windows of the broken version reopened on the kept one")
        self.assertTrue(self.system.call_args.args[3].startswith(messages.cc("pinned", new="9.9.9", old="9.9.8")))
        self.assertEqual(cc_health.pinned_env(), {"DISABLE_AUTOUPDATER": "1"})

    def test_kept_version_failing_too_steps_down(self):
        cc_health.keep("9.9.8", self.old)
        bad = {"ok": False, "step": 3, "why": "回覆之後畫面上找不到輸入框", "screen": "?"}
        w = self.watch(bad, bad, dict(bad))
        self.settle(w, w.failed("tmux", 1, why="x"))
        self.assertIsNone(cc_health.pin())
        self.assertEqual(list(cc_health.broken()), ["tmux"])

    def test_claude_that_does_not_start(self):
        cc_health.keep("9.9.8", self.old)
        w = self.watch({"ok": False, "why": "沒有拿到結果"}, {"ok": True}, {"ok": True})  # this version's check, then
        # the kept one's probe and print turn
        self.settle(w, w.failed("start", 1, why="exit 1"))
        self.assertIsNone(cc_health.pin(), "once is not enough")
        self.settle(w, w.failed("start", 2, why="exit 1"))
        self.assertEqual(cc_health.pin()["version"], "9.9.8")

    def test_non_zero_exits_this_version_passes(self):
        """#code-review on 0c0b58d: exits that are none of the version's (a conversation file gone) do not pin."""
        cc_health.keep("9.9.8", self.old)
        w = self.watch({"ok": True})
        self.settle(w, w.failed("start", 1, why="No conversation found"))
        self.settle(w, w.failed("start", 2, why="No conversation found"))
        self.assertEqual(w.probe.runs, 1, "this version checked")
        self.assertIsNone(cc_health.pin())

    def test_a_turn_that_worked_ends_the_run(self):
        w = self.watch()
        self.settle(w, w.failed("start", 1, why="exit 1"))
        w.worked(1, "json")
        self.settle(w, w.failed("start", 1, why="exit 1"))
        self.assertEqual(w.probe.runs, 0, "1, a success, 1: not 2")

    def test_a_new_version_that_passes_lets_the_pin_go(self):
        cc_health.keep("9.9.8", self.old)
        cc_health.set_pin(cc_health.last_good(), "9.9.9")
        w = self.watch({"ok": True}, {"ok": True})
        with mock.patch.object(cc_health, "version_of", lambda path: "9.9.10" if path == cc_health.config.CLAUDE else "9.9.8"):
            self.settle(w, w.tick())
        self.assertIsNone(cc_health.pin())
        self.over.assert_awaited_once()

    def test_the_same_new_version_is_not_tried(self):
        cc_health.keep("9.9.8", self.old)
        cc_health.set_pin(cc_health.last_good(), "9.9.9")
        w = self.watch()
        with mock.patch.object(cc_health, "version_of", lambda path: "9.9.9"):
            self.settle(w, w.tick())
        self.assertEqual(w.probe.runs, 0)

    def test_a_kept_copy_that_changed_is_no_pin(self):
        cc_health.keep("9.9.8", self.old)
        cc_health.set_pin(cc_health.last_good(), "9.9.9")
        path = cc_health.pin()["path"]
        with open(path, "r+b") as f:
            f.write(b"X")  # same size, other bytes
        w = self.watch()
        with mock.patch.object(cc_health, "version_of", lambda path: "9.9.9"):
            self.settle(w, w.tick())
        self.assertIsNone(cc_health.pin())

    def test_reminded_once_after_the_days(self):
        import time
        cc_health.keep("9.9.8", self.old)
        cc_health.set_pin(cc_health.last_good(), "9.9.9")
        w = self.watch()
        w.sha_checked = time.time()
        ch = mock.Mock()
        ch.id = 31
        ch.send = mock.AsyncMock(return_value=mock.Mock(id=900))

        async def where():
            return ch
        w.bridge.notify_channel = where
        with mock.patch.object(cc_health, "version_of", lambda path: "9.9.9"), \
                mock.patch.object(calls, "allowed", lambda: None):
            self.settle(w, w.tick(now=time.time() + 6 * 86400))
            ch.send.assert_not_called()
            self.settle(w, w.tick(now=time.time() + 8 * 86400))
            self.settle(w, w.tick(now=time.time() + 9 * 86400))
        ch.send.assert_awaited_once()
        self.assertTrue(ch.send.call_args.args[0].startswith(messages.cc("pin_long", old="9.9.8", days=7)))
        ids = [b.custom_id for b in ch.send.call_args.kwargs["view"].children]
        self.assertEqual(ids, [cc_watch.PIN_KEEP, cc_watch.PIN_NEW])
        self.assertEqual(cc_health.pin_asking()["card"], [31, 900], "where it is kept: --waiting lists it there (C-187)")

    def test_a_new_version_is_drilled_once(self):
        w = self.watch()
        with mock.patch.object(cc_health, "version_of", lambda path: "9.9.9"):
            asyncio.run(w.new_version(10 ** 10))
            asyncio.run(w.new_version(10 ** 10 + 2 * 3600))
        self.assertEqual(self.drill.await_count, 1, "drilled once, passed: not again")
        self.assertIn(messages.cc("drilled", version="9.9.9", start="✅", screen="✅", print="✅"), w.bridge.host.audit.call_args.args[0])

    def test_a_new_version_failing_twice_takes_the_kept_one(self):
        cc_health.keep("9.9.8", self.old)
        self.drill.return_value = {"ok": False, "started": True, "screen": False, "print": True, "why": "沒接住的步驟：0"}
        w = self.watch({"ok": False, "step": 1, "why": "畫面上一直找不到輸入框"}, {"ok": True}, {"ok": True})  # this
        # version with the real login, then the kept version's probe and print turn
        with mock.patch.object(cc_health, "version_of", lambda path: "9.9.9"):
            asyncio.run(w.new_version(10 ** 10))
            self.assertIsNone(cc_health.pin(), "once: tried again an hour on")
            self.settle(w, w.new_version(10 ** 10 + 3600))
            self.settle(w, w.new_version(10 ** 10 + 7200))
        self.assertEqual(self.drill.await_count, 2)
        self.assertEqual(cc_health.pin()["version"], "9.9.8")

    def test_only_step_0_says_the_new_version_is_broken(self):
        """#code-review and #code-review-2 on f743a61: steps 1-10 rehearse the bot (and step 10 the older version)."""
        cc_health.keep("9.9.8", self.old)
        self.drill.return_value = {"ok": False, "started": True, "screen": True, "print": True, "why": "沒接住的步驟：4、10"}
        w = self.watch()
        with mock.patch.object(cc_health, "version_of", lambda path: "9.9.9"):
            for hour in range(3):
                self.settle(w, w.new_version(10 ** 10 + hour * 3600))
        self.assertIsNone(cc_health.pin())
        self.assertEqual(w.probe.runs, 0, "nothing checked, nothing pinned")
        self.assertIn("4、10", w.bridge.host.audit.call_args.args[0])

    def test_step_0_failing_on_the_fake_api_but_not_for_real(self):
        cc_health.keep("9.9.8", self.old)
        self.drill.return_value = {"ok": False, "started": True, "screen": True, "print": False, "why": "沒接住的步驟：0"}
        w = self.watch({"ok": True})  # this version's print turn with the real login passes
        with mock.patch.object(cc_health, "version_of", lambda path: "9.9.9"):
            self.settle(w, w.new_version(10 ** 10))
            self.settle(w, w.new_version(10 ** 10 + 3600))
        self.assertIsNone(cc_health.pin())
        self.assertEqual(w.probe.runs, 1)

    def test_the_drill_runs_off_the_tick(self):
        w = self.watch()
        gate = asyncio.Event()

        async def slow():
            await gate.wait()
            return {"ok": True, "started": True, "screen": True, "print": True}
        self.drill.side_effect = slow

        async def go():
            with mock.patch.object(cc_health, "version_of", lambda path: "9.9.9"):
                await asyncio.wait_for(w.tick(now=10 ** 10), 1)  # comes back while the drill still runs
                for _ in range(5):
                    await asyncio.sleep(0)
                await asyncio.wait_for(w.tick(now=10 ** 10 + 2 * 3600), 1)
                for _ in range(5):
                    await asyncio.sleep(0)
                self.assertEqual(self.drill.await_count, 1, "one drill at a time")
                gate.set()
                await w.drilling
        asyncio.run(go())

    def test_a_drill_still_running_counts_nothing(self):
        self.drill.return_value = {"busy": True}
        w = self.watch()
        with mock.patch.object(cc_health, "version_of", lambda path: "9.9.9"):
            asyncio.run(w.new_version(10 ** 10))
        self.assertNotIn("drilled", cc_health.entry())

    def test_no_drill_on_a_busy_machine(self):
        w = self.watch()
        with mock.patch.object(cc_health, "version_of", lambda path: "9.9.9"), \
                mock.patch.object(cc_health, "busy_machine", lambda: "load 99"):
            asyncio.run(w.new_version(10 ** 10))
        self.drill.assert_not_called()

    def press(self, w, cid, owner=True):
        from bot import config
        it = mock.Mock()
        it.data = {"custom_id": cid}
        it.user.id = config.OWNER_ID if owner else -1
        it.response.edit_message = mock.AsyncMock()
        it.response.send_message = mock.AsyncMock()
        it.followup.send = mock.AsyncMock()
        self.assertTrue(asyncio.run(w.on_interaction(it)))
        return it

    def test_the_two_buttons(self):
        cc_health.keep("9.9.8", self.old)
        cc_health.set_pin(cc_health.last_good(), "9.9.9")
        cc_health.pin_reminded(5)
        w = self.watch()
        it = self.press(w, cc_watch.PIN_KEEP, owner=False)
        it.response.edit_message.assert_not_called()
        self.press(w, cc_watch.PIN_KEEP)
        self.assertNotIn("reminded", cc_health.pin())
        self.press(w, cc_watch.PIN_NEW)
        self.assertIsNone(cc_health.pin())
        self.assertEqual(cc_health.broken_version("tmux"), "9.9.9", "the new version, in simple mode")

    def test_another_channels_claude_answers_the_reminder(self):
        """C-187 (manager 11-12): the reminder is listed by --waiting in the channel it was posted in and answered by
        --answer p-ccpin --pick <a button's label>, with a why in #audit and how to change it back."""
        import types
        from bot import agentanswer
        cc_health.keep("9.9.8", self.old)
        cc_health.set_pin(cc_health.last_good(), "9.9.9")
        w = self.watch()
        audits, edits, sent = [], [], []

        class Card(object):
            async def edit(self, view="x"):
                edits.append(view)

        class Ch(object):
            id, name = 31, "notifications"

            def get_partial_message(self, mid):
                edits.append(mid)
                return Card()

            async def send(self, text, silent=False):
                sent.append(text)

        async def audit(text):
            audits.append(text)
        bridge = types.SimpleNamespace(cc=w, menus=None, host=types.SimpleNamespace(chan=lambda ch: {}, audit=audit))
        lead, ch = types.SimpleNamespace(id=7, name="lead"), Ch()
        self.assertEqual(agentanswer.items(bridge, ch), [], "not reminded yet")
        cc_health.pin_reminded(5)
        cc_health.pin_card(31, 900)
        (got,) = agentanswer.items(bridge, ch)
        self.assertEqual(got[:2], ("p-ccpin", "pin"))
        self.assertIn("- 繼續停 7 天\n- 改用新版的簡易模式", got[2])
        self.assertEqual(agentanswer.items(bridge, types.SimpleNamespace(id=32, name="x")), [], "only where it was posted")
        go = lambda pick: asyncio.run(agentanswer.answer(bridge, ch, lead, {"answer": "p-ccpin", "pick": [pick],  # noqa: E731
                                                                            "why": "owner said keep", "verified": True}))
        ok, said = go("upgrade")
        self.assertFalse(ok)
        self.assertIn("--pick 要是「繼續停 7 天」或「改用新版的簡易模式」", said)
        ok, said = go("繼續停 7 天")
        self.assertTrue(ok, said)
        self.assertNotIn("reminded", cc_health.pin(), "kept: asked again later")
        self.assertEqual(edits, [900, None], "the buttons come off")
        self.assertEqual(sent, [messages.cc("pin_kept", old="9.9.8", days=7) + "\n-# " +
                                messages.cc("pin_by_keep", by="#lead 的 Claude")])
        self.assertEqual(w.bridge.host.audit.call_args.args[0],
                         "C-162：停在舊版的提醒，#lead 的 Claude（查證過） 選了繼續停（為什麼：owner said keep）")
        ok, said = go("繼續停 7 天")
        self.assertEqual((ok, said), (False, "沒送：" + agentanswer.GONE_PIN), "answered already")
        cc_health.pin_reminded(6)
        cc_health.pin_card(31, 901)
        ok, said = go("改用新版的簡易模式")
        self.assertTrue(ok, said)
        self.assertIsNone(cc_health.pin())
        self.assertEqual(cc_health.broken_version("tmux"), "9.9.9")
        self.assertIn(messages.cc("pin_by_new", by="#lead 的 Claude"), sent[-1])

    def test_cctest(self):
        new = self.claude("9.9.9", b"new claude")
        told = []

        async def say(text):
            told.append(text)
        w = self.watch({"ok": True}, {"ok": True}, {"ok": True})
        w.probe.text_check = w.probe.print_check
        with mock.patch.object(cc_health.config, "CLAUDE", new), mock.patch.object(cc_health, "version_of", lambda path: "9.9.9"):
            said = asyncio.run(w.cctest(say))
        self.assertEqual(told, [messages.cc("cctest_start", version="9.9.9")])
        self.assertEqual(said, messages.cc("cctest_ok", version="9.9.9"))
        self.assertEqual(cc_health.last_good()["version"], "9.9.9")
        w = self.watch({"ok": True}, {"ok": False, "why": "沒有拿到結果"}, {"ok": True})
        w.probe.text_check = w.probe.print_check
        with mock.patch.object(cc_health.config, "CLAUDE", new), mock.patch.object(cc_health, "version_of", lambda path: "9.9.10"):
            said = asyncio.run(w.cctest(say))
        self.assertIn("簡易模式：沒有拿到結果", said)
        self.assertEqual(cc_health.last_good()["version"], "9.9.9", "a failed check changes nothing")


class Reopen(Health):
    """#code-review-2 on 0c0b58d: after a pin, windows running another version are reopened on the kept one."""

    def test_marked_by_version(self):
        from bot import tmux_bridge
        b = tmux_bridge.TmuxBridge.__new__(tmux_bridge.TmuxBridge)
        states = {"1": {"tmux": "dc-a", "cc_version": "9.9.9"}, "2": {"tmux": "dc-b", "cc_version": "9.9.8"},
                  "3": {"pane": {"socket": "/s", "id": "%3"}, "cc_version": "9.9.9"}, "4": {"tmux": "dc-d"}}
        b.host = mock.Mock(channels_state=states)
        b.reopen_off("9.9.8")
        self.assertEqual([k for k, st in states.items() if st.get("cc_stale")], ["1"])
        b.host.save.assert_called_once()

    def test_its_turn_ends_first_up_to_a_limit(self):
        from bot import tmux_bridge
        b = tmux_bridge.TmuxBridge.__new__(tmux_bridge.TmuxBridge)
        st = {"busy": True}
        pane = mock.Mock()
        pane.alive = mock.AsyncMock(return_value=True)

        async def go():
            async def ends():
                await asyncio.sleep(0.05)
                st["busy"] = False
            t = asyncio.ensure_future(ends())
            with mock.patch.object(tmux_bridge.asyncio, "sleep", fast()):
                await asyncio.wait_for(b.stale_turn_done(mock.Mock(), st, pane), 2)
            await t
        asyncio.run(go())
        st["busy"] = True
        b.say = mock.AsyncMock()
        with mock.patch.object(tmux_bridge, "STALE_WAIT_SECS", 0.05), mock.patch.object(tmux_bridge, "STALE_SAY_SECS", 0), \
                mock.patch.object(tmux_bridge.asyncio, "sleep", fast()):
            asyncio.run(asyncio.wait_for(b.stale_turn_done(mock.Mock(), st, pane), 2))
        b.say.assert_awaited_once()  # (said why it waits: #code-review-2 on 7780fb3)


def fast():
    real = asyncio.sleep

    async def sleep(secs, *a, **k):
        return await real(min(secs, 0.01))
    return sleep


class GlobalHook(unittest.TestCase):
    def test_the_global_hook_reports_nothing_of_the_probe(self):
        root = tempfile.mkdtemp()
        env = dict(os.environ, ALT_PROBE="1", HOME=root, DCBOT_ROOT=root, DCBOT_CHANNEL="")
        r = subprocess.run([sys.executable, os.path.join(DCBOT, "tools", "dchook"), "--global"], input=b'{"hook_event_name": "Stop"}',
                           env=env, capture_output=True, timeout=30)
        self.assertEqual(r.returncode, 0)
        self.assertFalse(os.path.exists(os.path.join(root, "state", "events")) and os.listdir(os.path.join(root, "state", "events")))


if __name__ == "__main__":
    unittest.main()
