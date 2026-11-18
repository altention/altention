"""C-162 step 1: the layers (tmux → print → text) a machine's channels are held to, the file the bot runs, and the
print turns run lower (design: instance docs/refactor/c162-design.md, sections 1, 2.2, 4, 10)."""
import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))))
from altention import testing as _testing  # noqa: E402
_testing.guard_home()  # tests never touch the real home (altention/testing.py; 11-08: one left a real dcremote-owner)
import asyncio
import contextlib
import json
import os
import stat
import tempfile
import unittest
from unittest import mock

from bot import cc_health, claude_bridge, config, messages
from tests import tmuxguard  # noqa: F401  (no test reaches a real tmux server)


class Health(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        for p in (mock.patch.object(cc_health, "path", lambda: os.path.join(self.tmp, "cc_health.json")),
                  mock.patch.object(cc_health, "machine", lambda: "host-a")):
            p.start()
            self.addCleanup(p.stop)


class Levels(Health):
    def test_nothing_written_is_the_top_layer(self):
        self.assertEqual(cc_health.level(), 1)
        self.assertEqual(cc_health.effective("tmux", {"tmux": "dc-x"}), "tmux")

    def test_an_unreadable_file_is_the_top_layer(self):
        for body in ("{", "[]", json.dumps({"machines": []}), json.dumps({"machines": {"host-a": {"level": 7}}})):
            with open(cc_health.path(), "w") as f:
                f.write(body)
            self.assertEqual(cc_health.level(), 1, body)

    def test_held_and_let_go(self):
        cc_health.set_level(2, version="9.9.9", now=5)
        self.assertEqual(cc_health.broken(), {"tmux": {"version": "9.9.9", "since": 5}})
        self.assertEqual(cc_health.effective("tmux", {"tmux": "dc-x"}), "print")
        self.assertEqual(cc_health.effective("print", {"tmux": "dc-x"}), "print")
        cc_health.set_level(3, version="9.9.9")
        self.assertEqual(cc_health.effective("tmux", {"tmux": "dc-x"}), "text")
        self.assertEqual(cc_health.effective("print", {}), "text")
        cc_health.set_level(1)
        self.assertEqual(cc_health.broken(), {})
        self.assertEqual(cc_health.effective("tmux", {"tmux": "dc-x"}), "tmux")

    def test_keyed_by_machine(self):
        cc_health.set_level(2, version="9.9.9")
        with open(cc_health.path()) as f:
            self.assertEqual(list(json.load(f)["machines"]), ["host-a"])
        with mock.patch.object(cc_health, "machine", lambda: "host-b"):
            self.assertEqual(cc_health.level(), 1)

    def test_only_this_machines_conversations_the_bot_opened(self):
        cc_health.set_level(2, version="9.9.9")
        held = [{"tmux": "dc-x"}, {"home": {"socket": "/s", "session": "w"}, "pane": {"socket": "/s", "id": "%1"}}, {}]
        free = [{"home": {"host": "host-b", "session": "w"}},  # another machine: print does not run there yet
                {"pane": {"socket": "/s", "id": "%3"}},  # the owner's own pane, attached
                {"pane": {"host": "host-b", "socket": "/s", "id": "%3"}},
                {"kind": "terminal", "tmux": "dc-t"}, {"kind": "files"}, {"plain": True}]
        for st in held:
            self.assertEqual(cc_health.effective("tmux", st), "print", st)
        for st in free:
            self.assertEqual(cc_health.effective("tmux", st), "tmux", st)


class Binary(Health):
    def test_the_configured_file_unless_one_is_pinned_and_there(self):
        self.assertEqual(cc_health.binary(), config.CLAUDE)
        kept = os.path.join(self.tmp, "9.9.8")
        cc_health.save({"machines": {"host-a": {"pin": {"path": kept}}}})
        self.assertEqual(cc_health.binary(), config.CLAUDE, "a pinned file that is gone is no pin")
        open(kept, "w").close()
        self.assertEqual(cc_health.binary(), kept)

    def test_every_place_the_bot_runs_claude_asks_it(self):
        """Section 3.2: one function says which file; nothing else in the bot names config.CLAUDE."""
        here = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bot")
        for name in sorted(os.listdir(here)):
            if name.endswith(".py") and name not in ("config.py", "cc_health.py", "cc_drill.py"):  # (the drill puts
                # its stand-in versions there itself)
                with open(os.path.join(here, name), encoding="utf-8") as f:
                    self.assertNotIn("config.CLAUDE", f.read(), name)


def fake_claude(folder, body):
    path = os.path.join(folder, "claude")
    with open(path, "w") as f:
        f.write("#!/bin/sh\ncat >/dev/null\n" + body)
    os.chmod(path, os.stat(path).st_mode | stat.S_IEXEC)
    return path


class Print(Health):
    def test_command(self):
        cmd, sid = claude_bridge.command("text", "s-1", "note", "auto", model="haiku", dirs=["/a"])
        self.assertEqual(cmd[:6], [config.CLAUDE, "-p", "--model", "haiku", "--output-format", "text"])
        self.assertNotIn("--verbose", cmd)
        self.assertEqual(cmd[-2:], ["--resume", "s-1"])
        self.assertEqual(sid, "s-1")
        self.assertIn("--settings", cmd, "the deny rules hold at every layer")
        cmd, sid = claude_bridge.command("stream-json", None, "note", "auto")
        self.assertEqual(cmd[3:7], [config.MODEL, "--output-format", "stream-json", "--verbose"])
        self.assertEqual(cmd[-2:], ["--session-id", sid])

    def run_text(self, body):
        with mock.patch.object(cc_health, "binary", lambda data=None: fake_claude(self.tmp, body)):
            return asyncio.run(claude_bridge.run_text("hi", "s-1", self.tmp, "note", "auto", channel_id=42))

    def test_text_reply(self):
        res = self.run_text("echo '  the reply  '\n")
        self.assertEqual((res.text, res.error, res.session_id), ("the reply", None, "s-1"))

    def test_nothing_written_is_no_reply(self):
        res = self.run_text("exit 0\n")
        self.assertTrue(res.error)
        res = self.run_text("echo 'not logged in' >&2\nexit 1\n")
        self.assertIn("not logged in", res.error)


class Chan(object):
    def __init__(self):
        self.id, self.name, self.sent = 42, "lap", []

    async def send(self, text, **kw):
        self.sent.append(text)

    def typing(self):
        return contextlib.AsyncExitStack()


class Turn(Health):
    """main.Bot.turn() run lower: the channel's window closed once, the channel's model, the words on the reply."""

    def turn(self, st, **level):
        from bot import main
        if level:
            cc_health.set_level(level["level"], version="9.9.9")
        bot = mock.Mock()
        bot.mode = lambda s: main.Bot.mode(bot, s)
        bot.held = lambda s: main.Bot.held(bot, s)
        bot.chan.return_value = st
        bot.note.return_value = "note"
        bot.tmux.chosen_for = mock.AsyncMock(return_value=("haiku", "auto"))
        bot.tmux.close = mock.AsyncMock()
        bot.tmux.rt.return_value = mock.Mock(lock=asyncio.Lock())
        bot.tmux.cc.maybe_back = mock.AsyncMock()
        bot.tmux.cc.failed = mock.AsyncMock()
        bot.audit = mock.AsyncMock()
        bot.procs, bot.active = {}, 0
        ch, msg = Chan(), mock.Mock()
        msg.channel = ch
        res = claude_bridge.TurnResult("the reply", "s-1")
        runs = {"turn": mock.AsyncMock(return_value=res), "text": mock.AsyncMock(return_value=res)}
        with mock.patch.object(main, "run_turn", runs["turn"]), mock.patch.object(main, "run_text", runs["text"]), \
                mock.patch.object(main.attachments, "folders", mock.AsyncMock(return_value=[])):
            asyncio.run(main.Bot.turn(bot, msg, "hello"))
        return bot, ch, runs

    def test_a_print_channel_as_before(self):
        bot, ch, runs = self.turn({"mode": "print", "cwd": self.tmp})
        runs["turn"].assert_called_once()
        self.assertEqual(runs["turn"].call_args.kwargs["model"], "haiku", "the channel's model (design section 7)")
        bot.tmux.close.assert_not_called()
        self.assertNotIn(messages.cc("footer_print"), ch.sent[-1])

    def test_held_to_print(self):
        st = {"mode": "tmux", "tmux": "dc-lap", "cwd": self.tmp, "session_id": "s-1"}
        bot, ch, runs = self.turn(st, level=2)
        runs["turn"].assert_called_once()  # (the window is closed before, in print_turn: LeaveWindow)
        self.assertIn(messages.cc("footer_print"), ch.sent[-1])
        self.assertIn(messages.cc("missing"), ch.sent[-1], "what is missing, on the first reply")
        bot, ch, runs = self.turn(st)
        self.assertIn(messages.cc("footer_print"), ch.sent[-1])
        self.assertNotIn(messages.cc("missing"), ch.sent[-1], "once")

    def test_held_to_text(self):
        st = {"mode": "tmux", "tmux": "dc-lap", "cwd": self.tmp}
        bot, ch, runs = self.turn(st, level=3)
        runs["text"].assert_called_once()
        runs["turn"].assert_not_called()
        self.assertIn(messages.cc("footer_text"), ch.sent[-1])
        self.assertIn(messages.cc("missing"), ch.sent[-1])

    def test_an_attached_pane_is_not_held(self):
        from bot import main
        cc_health.set_level(2, version="9.9.9")
        bot = mock.Mock()
        self.assertEqual(main.Bot.mode(bot, {"pane": {"socket": "/s", "id": "%3"}}), "tmux")


class Handed(Health):
    """Words handed to a channel's Claude other than by the owner's message (a post, a button, a message sent
    again) go through TmuxBridge.submit(): a channel run in print or held down gets a turn, not a window opened."""

    def test_handed_over_before_the_turn_ends(self):
        """submit() comes back at once, the turn queued (a post's spool file goes then: a restart while the turn
        waits or runs neither posts it again nor runs it again, #code-review on 7c0ac52)."""
        for mode in ("print", "text"):
            got, started = self.hand_over(mode)
            self.assertIsNone(got)
            self.assertEqual(started, ["from #lap"], "queued, and running")

    def hand_over(self, mode):
        from bot import main, tmux_bridge
        bot = mock.Mock()
        bot.mode = lambda st: mode
        bot.chan.return_value = {}
        never = asyncio.Event()
        started = []

        async def print_turn(message, text):
            started.append(text)
            await never.wait()  # a turn that has not ended
        bot.print_turn = print_turn
        bridge = tmux_bridge.TmuxBridge.__new__(tmux_bridge.TmuxBridge)
        bridge.host, bridge.rt = bot, mock.Mock()

        async def go():
            bot.loop = asyncio.get_running_loop()
            bot.queue_turn = lambda m, t: main.Bot.queue_turn(bot, m, t)
            got = await asyncio.wait_for(bridge.submit(mock.Mock(), "from #lap"), 2)
            await asyncio.sleep(0)
            return got
        return asyncio.run(go()), started

    def test_a_queued_turn_that_finds_tmux_again_is_typed(self):
        """`!mode retry` while a turn waited: it is not run in print beside the window, it is typed into it."""
        from bot import main
        bot = mock.Mock()
        bot.locks = {}
        bot.mode = lambda st: "tmux"
        bot.chan.return_value = {}
        bot.turn = mock.AsyncMock()
        bot.tmux.submit = mock.AsyncMock()
        msg = mock.Mock()

        async def go():
            bot.sem = asyncio.Semaphore(4)
            await main.Bot.print_turn(bot, msg, "hello")
        asyncio.run(go())
        bot.turn.assert_not_called()
        bot.tmux.submit.assert_awaited_once_with(msg, "hello")

    def test_the_window_waits_for_the_print_queue(self):
        """A window opened on the conversation waits for the channel's print turn to end (#code-review-2 on
        7c0ac52: two Claudes on one conversation)."""
        from bot import tmux_bridge
        bridge = tmux_bridge.TmuxBridge.__new__(tmux_bridge.TmuxBridge)
        order = []

        async def go():
            lock = asyncio.Lock()
            bridge.host = mock.Mock(locks={"42": lock})
            ch = mock.Mock(id=42)

            async def turn():
                async with lock:
                    await asyncio.sleep(0.05)
                    order.append("turn ended")
            t = asyncio.ensure_future(turn())
            await asyncio.sleep(0)
            await bridge.print_done(ch)
            order.append("window")
            await t
        asyncio.run(go())
        self.assertEqual(order, ["turn ended", "window"])

    def test_tmux_types_as_ever(self):
        from bot import tmux_bridge
        host = mock.Mock()
        host.mode = lambda st: "tmux"
        host.print_turn = mock.AsyncMock()
        bridge = tmux_bridge.TmuxBridge.__new__(tmux_bridge.TmuxBridge)
        bridge.host = host
        bridge.rt = mock.Mock()
        with mock.patch.object(tmux_bridge.TmuxBridge, "btw_open", mock.AsyncMock()) as btw:
            asyncio.run(bridge.submit(mock.Mock(), "/btw hi"))
        btw.assert_awaited_once()
        host.print_turn.assert_not_called()


class LeaveWindow(Health):
    """The first turn of a channel held down closes its window, after a turn running there ends, before a place among
    the print turns is taken (#code-review and #code-review-2 on e30eba7)."""

    def bot(self, st, alive=True):
        from bot import main
        bot = mock.Mock()
        bot.tmux.rt.return_value = mock.Mock(lock=asyncio.Lock())
        bot.tmux.close = mock.AsyncMock()
        bot.tmux.live_target = mock.AsyncMock(return_value="dc-lap" if alive else None)
        return main, bot

    def test_marked_then_closed(self):
        main, bot = self.bot({})
        st = {"mode": "tmux", "tmux": "dc-lap"}
        asyncio.run(main.Bot.leave_window(bot, mock.Mock(), st, "print"))
        self.assertTrue(st["cc_stale"])
        bot.tmux.close.assert_awaited_once()
        bot.tmux.live_target.return_value = None  # (closed)
        asyncio.run(main.Bot.leave_window(bot, mock.Mock(), st, "print"))
        bot.tmux.close.assert_awaited_once()  # once
        bot.tmux.live_target.return_value = "dc-lap"  # cc_stale, yet a window there (a pin's mark): waited on, closed
        asyncio.run(main.Bot.leave_window(bot, mock.Mock(), st, "print"))
        self.assertEqual(bot.tmux.close.await_count, 2, "#code-review on 7780fb3")
        st = {"mode": "print"}
        asyncio.run(main.Bot.leave_window(bot, mock.Mock(), st, "text"))
        self.assertNotIn("cc_stale", st, "a print channel has no window of its own to leave")

    def test_waiting_on_each_other_ends(self):
        """#code-review on e36e823: a message holding rt.lock waits for the channel's queue this turn holds; the
        turn stops waiting for rt.lock after WINDOW_LOCK_SECS and goes on in print (cc_stale marked)."""
        main, bot = self.bot({})
        st = {"mode": "tmux", "tmux": "dc-lap"}

        async def go():
            await bot.tmux.rt.return_value.lock.acquire()  # held by the message, which waits for the queue
            await asyncio.wait_for(main.Bot.leave_window(bot, mock.Mock(), st, "print"), 2)
        with mock.patch.object(main, "WINDOW_LOCK_SECS", 0.05):
            asyncio.run(go())
        self.assertTrue(st["cc_stale"])
        bot.tmux.close.assert_not_called()

    def test_a_window_gone_stops_the_wait(self):
        main, bot = self.bot({}, alive=False)
        st = {"mode": "tmux", "tmux": "dc-lap", "busy": True}
        asyncio.run(asyncio.wait_for(main.Bot.leave_window(bot, mock.Mock(), st, "print"), 2))
        bot.tmux.close.assert_awaited_once()

    def test_another_channel_runs_while_one_waits(self):
        """One channel waits for its window's long turn; another held down channel gets its turn meanwhile, and the
        waiting one says why it waits."""
        from bot import main
        ran = []
        bot = mock.Mock()
        bot.locks, bot.sem_waiting = {}, 0
        states = {1: {"mode": "tmux", "tmux": "dc-a", "busy": True}, 2: {"mode": "tmux", "tmux": "dc-b"}}
        bot.chan = lambda ch: states[ch.id]
        bot.mode = lambda st: "print"
        bot.held = lambda st: "print"
        bot.leave_window = lambda ch, st, held: main.Bot.leave_window(bot, ch, st, held)
        bot.tmux.rt.side_effect = lambda ch, locks={}: locks.setdefault(ch.id, mock.Mock(lock=asyncio.Lock()))
        bot.tmux.close = mock.AsyncMock()
        bot.tmux.live_target = mock.AsyncMock(return_value="dc-a")

        async def turn(m, t):
            ran.append(m.channel.id)
        bot.turn = turn
        a, b = mock.Mock(id=1), mock.Mock(id=2)
        a.send = mock.AsyncMock()
        b.send = mock.AsyncMock()

        async def go():
            bot.sem = asyncio.Semaphore(1)
            first = asyncio.ensure_future(main.Bot.print_turn(bot, mock.Mock(channel=a), "x"))
            await asyncio.sleep(0.05)
            await asyncio.wait_for(main.Bot.print_turn(bot, mock.Mock(channel=b), "y"), 2)
            self.assertEqual(ran, [2], "the other channel ran while the first still waits")
            states[1]["busy"] = False
            await asyncio.wait_for(first, 5)
        with mock.patch.object(main, "WINDOW_SAY_SECS", 0), mock.patch.object(main.asyncio, "sleep", fast_sleep()):
            asyncio.run(go())
        self.assertEqual(ran, [2, 1])
        a.send.assert_awaited_once_with(messages.cc("wait_window", mode=messages.cc("mode_print")))


def fast_sleep():
    real = asyncio.sleep

    async def sleep(secs, *a, **k):
        return await real(min(secs, 0.01))
    return sleep


class Reopen(Health):
    def test_opened_clears_stale(self):
        from bot import tmux_bridge
        st = {"cc_stale": True, "cc_told": "print"}
        tmux_bridge.opened(st, mock.Mock(closed=[]))
        self.assertEqual(st, {}, "and what was said of the layer: said again when held down the next time")


if __name__ == "__main__":
    unittest.main()
