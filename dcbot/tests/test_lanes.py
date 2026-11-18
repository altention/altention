"""What a turn shows along the way goes out from its channel's lane, never from the round of hook events (lanes.py).
11-10: one channel's progress event waited 135 s on Discord in the round; another channel's SessionStart waited
behind it, and that channel's window, come up, was closed as not come up. Here a Discord request that hangs: the
round goes on, the other channels' events are handled, the reply comes after the lane's quotes and no "working…"
message is left behind; a new message that timed out is not sent again, an edit is tried once more."""
import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))))
from altention import testing as _testing  # noqa: E402
_testing.guard_home()  # tests never touch the real home (altention/testing.py)
import asyncio
import io
import os
import time
import unittest
from contextlib import redirect_stdout
from unittest import mock

import bot  # noqa: F401
from bot import lanes, tmux_bridge
from tests import test_hold
from tests.test_hold import call, reply, said
from altention.tests.connectors.claude_code.test_hooks import line
from tests.test_turn_end import Ch, Msg
from tests import tmuxguard  # noqa: F401,E402  (no test reaches a real tmux server)


class SlowMsg(Msg):
    def __init__(self, ch, *a, **kw):
        Msg.__init__(self, *a, **kw)
        self.ch, self.deleted, self.edits = ch, False, 0

    async def delete(self):
        self.deleted = True

    async def edit(self, content=None, view=None, **kw):
        self.edits += 1
        gate = self.ch.gate("edit", content or "")
        if gate is not None:
            await gate.wait()
        await Msg.edit(self, content=content, view=view, **kw)


class SlowCh(Ch):
    """A channel whose requests can hang: `hang[(what, start of the text)]` is an asyncio.Event they wait on (set:
    they go on). `tries` counts every send begun, `sent` only those Discord made."""
    def __init__(self):
        Ch.__init__(self)
        self.hang, self.tries = {}, []

    def gate(self, what, content):
        for (w, start), ev in self.hang.items():
            if w == what and content.startswith(start) and not ev.is_set():
                return ev
        return None

    async def send(self, content, view=None, silent=False, **kw):
        self.tries.append(content)
        gate = self.gate("send", content)
        if gate is not None:
            await gate.wait()
        self.sent.append(SlowMsg(self, len(self.sent) + 1, content, view, silent, kw))
        return self.sent[-1]


class Lanes(unittest.TestCase):
    path, write, fire = test_hold.Pump.path, test_hold.Pump.write, test_hold.Pump.fire  # (not Pump itself: its tests would run here again)

    def setUp(self):
        test_hold.Pump.setUp(self)
        self.saved_lane = (lanes.LANE_SECS, lanes.LANE_HOLD)
        lanes.LANE_SECS, lanes.LANE_HOLD = 0.2, 0.6
        get = self.host.get_channel

        def slow(cid):
            if cid not in self.host.chs:
                self.host.chs[cid] = SlowCh()
                self.host.chs[cid].id = cid
            return get(cid)
        self.host.get_channel = slow
        self.out = io.StringIO()

    def tearDown(self):
        lanes.LANE_SECS, lanes.LANE_HOLD = self.saved_lane
        test_hold.Pump.tearDown(self)

    def ch(self, cid):
        return self.host.get_channel(cid)

    async def around(self, later=0.0):
        """One round of the pump in this test's own event loop (the requests that hang outlive a round)."""
        self.now += later
        with mock.patch.object(tmux_bridge, "SPOOL", self.spool), mock.patch.object(time, "time", lambda: self.now), \
                redirect_stdout(self.out):
            began = time.monotonic()
            await self.bridge.pump_once()
            took = time.monotonic() - began
            await asyncio.sleep(0)
        return sorted(os.listdir(self.spool)), took

    async def settle(self):
        with mock.patch.object(time, "time", lambda: self.now), redirect_stdout(self.out):
            await self.bridge.lanes_done()

    def go(self, coro):
        async def run():
            try:
                await coro
            finally:
                for rt in self.bridge.rts.values():
                    if rt.typing is not None:
                        rt.typing.cancel()
        with redirect_stdout(self.out):  # (the lanes' lines come between the rounds too)
            asyncio.run(run())

    async def begin(self, channel, pid="p1"):
        self.write(channel, "")
        self.fire(channel, "SessionStart")
        self.fire(channel, "UserPromptSubmit", prompt="go", prompt_id=pid)
        self.assertEqual((await self.around())[0], [])
        self.write(channel, line("user", "go", promptId=pid))

    async def long_turn(self, channel):
        """A turn running for a minute already: its tool calls show the "working…" message."""
        await self.begin(channel)
        self.bridge.rt(self.ch(channel)).turn.t0 -= 60

    def test_a_working_message_that_hangs_holds_no_other_channel(self):
        gate = asyncio.Event

        async def run():
            await self.long_turn(5)
            await self.begin(6)
            self.ch(5).hang[("send", "處理中")] = gate()
            self.write(5, call("t1"))
            self.fire(5, "PreToolUse", tool_name="Bash", prompt_id="p1", tool_use_id="t1")
            self.write(6, reply("the reply of six"))
            self.fire(6, "Stop", last_assistant_message="the reply of six", prompt_id="p1")
            left, took = await self.around(1)
            self.assertEqual(left, [])
            self.assertLess(took, lanes.LANE_SECS, "the round does not wait on Discord")
            self.assertTrue(any(m.content.startswith("the reply of six") for m in self.ch(6).sent), "the other channel goes on")
            self.ch(5).hang[("send", "處理中")].set()
            await self.settle()
        self.go(run())

    def test_the_reply_comes_when_the_working_message_is_stuck_and_none_is_left(self):
        """Manager on the plan: the reply comes after the lane, and no "working…" message is left behind, even one
        whose request hung past the reply."""
        async def run():
            await self.long_turn(5)
            stuck = self.ch(5).hang[("send", "處理中")] = asyncio.Event()
            self.write(5, call("t1"))
            self.fire(5, "PreToolUse", tool_name="Bash", prompt_id="p1", tool_use_id="t1")
            await self.around(1)
            self.write(5, reply("the reply"))
            self.fire(5, "Stop", last_assistant_message="the reply", prompt_id="p1")
            left, _ = await self.around(0.1)
            self.assertEqual(len(left), 1, "the reply waits while its working message is on its way")
            await asyncio.sleep(lanes.LANE_SECS + 0.1)
            left, _ = await self.around(0.1)
            self.assertEqual(len(left), 1, "timed out, still on its way: held up to LANE_HOLD from its start")
            await asyncio.sleep(lanes.LANE_HOLD)
            left, _ = await self.around(0.1)
            self.assertEqual(left, [], "LANE_HOLD later: the reply goes")
            self.assertTrue(any(m.content.startswith("the reply") for m in self.ch(5).sent))
            stuck.set()  # Discord makes it after all, after the reply
            await asyncio.sleep(0.05)
            working = [m for m in self.ch(5).sent if m.content.startswith("處理中")]
            self.assertEqual(len(working), 1)
            self.assertTrue(working[0].deleted, "come after its turn ended: deleted at once")
            self.assertEqual(self.ch(5).tries.count(working[0].content), 1, "a new message is never sent twice")
            self.assertIn("[lane] #5：送處理中", self.out.getvalue())
        self.go(run())

    def test_a_working_message_that_comes_before_the_reply_is_deleted_by_it(self):
        async def run():
            await self.long_turn(5)
            slow = self.ch(5).hang[("send", "處理中")] = asyncio.Event()
            self.write(5, call("t1"))
            self.fire(5, "PreToolUse", tool_name="Bash", prompt_id="p1", tool_use_id="t1")
            await self.around(1)
            self.write(5, reply("the reply"))
            self.fire(5, "Stop", last_assistant_message="the reply", prompt_id="p1")
            await asyncio.sleep(lanes.LANE_SECS + 0.1)  # timed out ...
            slow.set()                                 # ... and made a moment later
            await asyncio.sleep(0.05)
            left, _ = await self.around(0.1)
            self.assertEqual(left, [])
            sent = self.ch(5).sent
            working = [m for m in sent if m.content.startswith("處理中")]
            self.assertEqual(len(working), 1)
            self.assertTrue(working[0].deleted, "the reply's turn end deletes it")
            self.assertLess(sent.index(working[0]), next(i for i, m in enumerate(sent) if m.content.startswith("the reply")))
        self.go(run())

    def test_the_quotes_come_before_the_reply(self):
        async def run():
            await self.begin(5)
            slow = self.ch(5).hang[("send", ">>> ")] = asyncio.Event()
            self.write(5, said("first I look") + call("t1"))
            self.fire(5, "PreToolUse", tool_name="Bash", prompt_id="p1", tool_use_id="t1")
            await self.around(0.1)
            self.write(5, reply("the reply"))
            self.fire(5, "Stop", last_assistant_message="the reply", prompt_id="p1")
            left, _ = await self.around(0.1)
            self.assertEqual(len(left), 1, "the reply waits for the quote on its way")
            asyncio.get_running_loop().call_later(0.05, slow.set)
            await self.settle()
            left, _ = await self.around(0.1)
            self.assertEqual(left, [])
            contents = [m.content for m in self.ch(5).sent]
            self.assertLess(contents.index(">>> first I look"), next(i for i, c in enumerate(contents) if c.startswith("the reply")))
        self.go(run())

    def test_a_quote_that_times_out_is_not_sent_again(self):
        async def run():
            await self.begin(5)
            self.ch(5).hang[("send", ">>> ")] = asyncio.Event()
            self.write(5, said("said once") + call("t1"))
            self.fire(5, "PreToolUse", tool_name="Bash", prompt_id="p1", tool_use_id="t1")
            await self.around(0.1)
            await self.settle()
            self.assertEqual(self.ch(5).tries.count(">>> said once"), 1)
            self.assertIn("不重送", self.out.getvalue())
            self.ch(5).hang[("send", ">>> ")].set()
            await asyncio.sleep(0.05)
            self.assertEqual([m.content for m in self.ch(5).sent].count(">>> said once"), 1, "it came, once")
        self.go(run())

    def test_an_edit_that_times_out_is_tried_once_more(self):
        async def run():
            await self.long_turn(5)
            self.write(5, call("t1"))
            self.fire(5, "PreToolUse", tool_name="Bash", prompt_id="p1", tool_use_id="t1")
            await self.around(1)
            await self.settle()
            working = next(m for m in self.ch(5).sent if m.content.startswith("處理中"))
            stuck = self.ch(5).hang[("edit", "處理中")] = asyncio.Event()
            asyncio.get_running_loop().call_later(lanes.LANE_SECS + 0.05, stuck.set)  # the second try goes through
            self.write(5, call("t2", "pwd"))
            self.fire(5, "PreToolUse", tool_name="Bash", prompt_id="p1", tool_use_id="t2")
            await self.around(10)
            await self.settle()
            self.assertEqual(working.edits, 2)
            self.assertIn("（71 秒）", working.content, "the second try's words are there")
            self.assertEqual(len([m for m in self.ch(5).sent if m.content.startswith("處理中")]), 1)
        self.go(run())

    def test_working_updates_not_sent_are_dropped_when_the_turn_ends(self):
        lane = lanes.Lane(5)
        done = []

        async def job(n):
            done.append(n)
        async def run():
            lane.items.extend([("q", lambda: job("quote"), False), ("p", lambda: job("progress"), True)])
            lane.drop_progress()
            lane.task = asyncio.ensure_future(lane.run())
            await lane.task
        asyncio.run(run())
        self.assertEqual(done, ["quote"])

    def test_one_working_update_waits_at_a_time(self):
        lane = lanes.Lane(5)

        async def run():
            gate = asyncio.Event()
            lane.put("first", gate.wait)
            lane.put("p", gate.wait, progress=True)
            lane.put("p", gate.wait, progress=True)
            self.assertEqual(len([i for i in lane.items if i[2]]), 1)
            gate.set()
            await lane.task
        asyncio.run(run())

    def test_a_restart_drops_what_the_lanes_hold_with_a_line(self):
        async def run():
            await self.long_turn(5)
            self.ch(5).hang[("send", ">>> ")] = asyncio.Event()
            self.write(5, said("one") + call("t1") + said("two") + call("t2"))
            self.fire(5, "PreToolUse", tool_name="Bash", prompt_id="p1", tool_use_id="t1")
            await self.around(0.1)
            lane = self.bridge.rt(self.ch(5)).lane
            lane.put("送引言", asyncio.Event().wait)  # one more waiting
            with redirect_stdout(self.out):
                await self.bridge.shutdown()
            self.assertEqual(len(lane.items), 0)
            self.assertIn("[lane] 重啟：", self.out.getvalue())
        self.go(run())


if __name__ == "__main__":
    unittest.main()
