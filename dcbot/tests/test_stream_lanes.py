"""C-174: an event from another machine's stream that waits on its channel's lane holds that channel only. Its later
events stay behind it, in order (a receipt of a typed message excepted, as on this machine); the machine's other
channels are handled meanwhile. Until C-174 the whole stream waited (lane_settled())."""
import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))))
from altention import testing as _testing  # noqa: E402
_testing.guard_home()

import asyncio
import collections
import contextlib
import io
import types
import unittest
from unittest import mock

from tests import tmuxguard  # noqa: F401
from bot import main, tmux_bridge


class FromStream(unittest.TestCase):
    def setUp(self):
        self.busy = set()       # channels whose lane still posts
        self.handled = []
        b = tmux_bridge.TmuxBridge.__new__(tmux_bridge.TmuxBridge)
        b.lane_waits = lambda ev: ev["channel"] in self.busy and ev.get("dc_kind") != "progress" and not ev.get("receipt")  # (as the real one)
        b.receipt_meanwhile = lambda ev: bool(ev.get("receipt"))

        async def handle(ev):
            self.handled.append(ev["n"])
        b.handle = handle
        self.b = b

    def test_only_that_channel_waits_and_its_events_keep_their_order(self):
        async def go():
            self.busy.add("1")
            await self.b.from_stream({"channel": "1", "n": "1-stop", "dc_kind": "turn_end"})   # waits: the lane posts
            await self.b.from_stream({"channel": "2", "n": "2-start", "dc_kind": "session_start"})  # another channel
            await self.b.from_stream({"channel": "1", "n": "1-tool", "dc_kind": "progress"})   # behind 1-stop all the same
            await self.b.from_stream({"channel": "1", "n": "1-receipt", "receipt": True})       # a receipt goes on
            await self.b.from_stream({"channel": "2", "n": "2-stop", "dc_kind": "turn_end"})
            self.assertEqual(self.handled, ["2-start", "1-receipt", "2-stop"], "the stream went on; channel 1 waits")
            self.busy.discard("1")  # its lane has posted what it held
            for _ in range(50):
                if len(self.handled) == 5:
                    break
                await asyncio.sleep(0.05)
            self.assertEqual(self.handled[3:], ["1-stop", "1-tool"], "in the order they came")
            self.assertEqual(self.b.lane_held, {}, "nothing left kept")
            await self.b.from_stream({"channel": "1", "n": "1-next", "dc_kind": "turn_end"})
            self.assertEqual(self.handled[-1], "1-next", "handled at once again")
        asyncio.run(go())

    def test_a_failing_event_does_not_stop_the_ones_behind_it(self):
        async def go():
            handle = self.b.handle

            async def failing(ev):
                if ev["n"] == "bad":
                    raise RuntimeError("x")
                await handle(ev)
            self.b.handle = failing
            self.busy.add("1")
            await self.b.from_stream({"channel": "1", "n": "bad", "dc_kind": "turn_end"})
            await self.b.from_stream({"channel": "1", "n": "after", "dc_kind": "turn_end"})
            self.busy.discard("1")
            for _ in range(50):
                if self.handled:
                    break
                await asyncio.sleep(0.05)
            self.assertEqual(self.handled, ["after"])
        with contextlib.redirect_stderr(io.StringIO()):  # (its traceback is printed)
            asyncio.run(go())


class LaneWaitsFailing(FromStream):
    def test_a_failing_lane_check_lets_the_event_go_on(self):
        """#code-review-2 on d46a429: lane_waits() raising in hand_on() ended its task, and every event kept behind
        lost. The event goes on instead."""
        async def go():
            self.busy.add("1")
            await self.b.from_stream({"channel": "1", "n": "first", "dc_kind": "turn_end"})
            await self.b.from_stream({"channel": "1", "n": "second", "dc_kind": "turn_end"})

            def broken(ev):
                raise RuntimeError("x")
            self.b.lane_waits = broken
            for _ in range(50):
                if len(self.handled) == 2:
                    break
                await asyncio.sleep(0.05)
            self.assertEqual(self.handled, ["first", "second"])
            self.assertEqual(self.b.lane_held, {})
        with contextlib.redirect_stderr(io.StringIO()):
            asyncio.run(go())


class Kept(unittest.TestCase):
    """#code-review on d46a429 (manager): a restart waits for what is kept (busy_with_work, at most RESTART_WAIT);
    what is still kept at a restart or a handover is said in #audit, channel by channel, with or without a reply."""

    def bridge(self):
        said = []

        async def audit(text):
            said.append(text)
        b = tmux_bridge.TmuxBridge.__new__(tmux_bridge.TmuxBridge)
        b.host = types.SimpleNamespace(audit=audit, get_channel=lambda cid: types.SimpleNamespace(name={1: "lab", 2: "notes"}[cid]))
        b.lane_held = {"2": collections.deque([{"dc_kind": "progress"}]),
                       "1": collections.deque([{"dc_kind": "turn_end"}, {"dc_kind": "progress"}])}
        b.lane_tasks, b.streams, b.rts = set(), {}, {}
        return b, said

    def test_a_restart_waits_while_anything_is_kept(self):
        tmux = types.SimpleNamespace(lane_held={})
        bot = types.SimpleNamespace(active=0, posts=None, tmux=tmux)
        self.assertFalse(main.Bot.busy_with_work(bot))
        tmux.lane_held["1"] = collections.deque([{"dc_kind": "turn_end"}])
        self.assertTrue(main.Bot.busy_with_work(bot))

    def test_a_restart_says_what_it_drops(self):
        b, said = self.bridge()

        async def go():
            task = asyncio.ensure_future(asyncio.sleep(60))
            b.lane_tasks.add(task)
            with contextlib.redirect_stdout(io.StringIO()):
                await b.shutdown()
            await asyncio.sleep(0)
            return task.cancelled()
        self.assertTrue(asyncio.run(go()), "the tasks are cancelled after it is said")
        self.assertEqual(said, [tmux_bridge.LANE_KEPT_RESTART % "#lab 2 則（含回覆）、#notes 1 則" + tmux_bridge.LANE_KEPT_REPLY_GONE])

    def test_without_a_reply_no_word_of_one(self):
        b, said = self.bridge()
        del b.lane_held["1"]
        asyncio.run(b.say_lane_kept())
        self.assertEqual(said, [tmux_bridge.LANE_KEPT_RESTART % "#notes 1 則"])

    def test_nothing_kept_nothing_said(self):
        b, said = self.bridge()
        b.lane_held = {}
        asyncio.run(b.say_lane_kept())
        asyncio.run(b.say_lane_kept(handover=True))
        self.assertEqual(said, [])

    def test_a_handover_says_it_too_before_closing(self):
        for name in ("fenced_out", "give_up"):
            b, said = self.bridge()
            order = []

            async def close(order=order):
                order.append("close")

            async def audit(text, order=order):
                order.append(text)
            b.host.audit = audit
            bot = types.SimpleNamespace(tmux=b, audit=mock.AsyncMock(), close=close, arm_end=lambda: None, end_now=lambda: None,
                                        lease=types.SimpleNamespace(write=mock.AsyncMock()), held_back=None, exit_code=0)
            with mock.patch.object(main, "stepped_down_note", lambda bot: ""):
                asyncio.run(main.Bot.fenced_out(bot, 5) if name == "fenced_out" else main.Bot.give_up(bot))
            self.assertEqual(order, [tmux_bridge.LANE_KEPT_HANDOVER % "#lab 2 則（含回覆）、#notes 1 則", "close"], name)

    def test_discord_hanging_does_not_hold_the_restart(self):
        """#code-review-2 on ba1bfa21: an audit that never answers. shutdown() goes on after LANE_KEPT_SAY_SECS and
        the kept events' task is cancelled (else watch_restart() would never get to close())."""
        b, said = self.bridge()

        async def hangs(text):
            await asyncio.Event().wait()
        b.host.audit = hangs

        async def go():
            task = asyncio.ensure_future(asyncio.sleep(60))
            b.lane_tasks.add(task)
            t0 = asyncio.get_running_loop().time()
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                await asyncio.wait_for(b.shutdown(), 5)
            took = asyncio.get_running_loop().time() - t0
            await asyncio.sleep(0)
            return took, task.cancelled()
        with mock.patch.object(tmux_bridge, "LANE_KEPT_SAY_SECS", 0.3):
            took, cancelled = asyncio.run(go())
        self.assertTrue(cancelled)
        self.assertLess(took, 2)

    def test_discord_failing_does_not_stop_the_restart(self):
        b, said = self.bridge()

        async def failing(text):
            raise RuntimeError("discord down")
        b.host.audit = failing
        with contextlib.redirect_stderr(io.StringIO()):
            asyncio.run(b.say_lane_kept())  # (no exception)


if __name__ == "__main__":
    unittest.main()
