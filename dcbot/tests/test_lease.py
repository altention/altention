"""C-163 step 1: the lease (bot/lease.py) and what the bot does with it; and that without a standby none of it runs.
No real Discord: #bot-lease is a stand-in channel whose edits get times from a stand-in Discord clock."""
import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))))
from altention import testing as _testing  # noqa: E402
_testing.guard_home()  # tests never touch the real home (altention/testing.py; 11-08: one left a real dcremote-owner)
import asyncio
import datetime
import json
import os
import tempfile
import unittest
from unittest import mock

from bot import lease, messages
from tests import tmuxguard  # noqa: F401  (no test reaches a real tmux server)

T0 = datetime.datetime(2026, 11, 8, 0, 0, tzinfo=datetime.timezone.utc)


class Discord(object):
    """A stand-in Discord time, moved by the tests."""
    def __init__(self):
        self.now = T0

    def tick(self, secs):
        self.now += datetime.timedelta(seconds=secs)


class Msg(object):
    def __init__(self, discord, content, author=1):
        self.discord, self.content = discord, content
        self.author = type("A", (), {"id": author})()
        self.created_at, self.edited_at = discord.now, None

    async def edit(self, content):
        if self.discord.refuse:
            raise OSError("403")
        self.content, self.edited_at = content, self.discord.now
        return self


class Chan(object):
    def __init__(self, discord):
        self.discord, self.msgs = discord, []

    def history(self, limit=20):
        async def gen():
            for m in list(self.msgs)[:limit]:
                yield m
        return gen()

    async def send(self, content):
        m = Msg(self.discord, content)
        self.msgs.append(m)
        return m


class Clock(object):
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def other(discord, machine, epoch, leader=True, lt=None):
    return Msg(discord, lease.worded(machine, epoch, leader, lt))


class Pure(unittest.TestCase):
    def test_enabled_only_with_a_standby(self):
        self.assertFalse(lease.enabled({}))
        self.assertFalse(lease.enabled({"host-a": {"roles": ["dcbot"]}, "host-b": {"roles": ["chatlog", "dcattach"]}}))
        self.assertTrue(lease.enabled({"host-a": {"roles": ["dcbot"]}, "host-b": {"roles": [lease.ROLE]}}))

    def test_who_holds_it(self):
        now = T0
        e = lambda m, ep, leader=True, lt=T0: {"machine": m, "epoch": ep, "leader": leader, "lt": lt}  # noqa: E731
        self.assertEqual(lease.leader_of([e("a", 1), e("b", 2)], now), "b", "the bigger epoch")
        self.assertEqual(lease.leader_of([e("a", 2), e("b", 2)], now, ["b", "a"]), "b", "a tie: the list's order")
        old = T0 - datetime.timedelta(seconds=lease.FRESH_SECS + 1)
        self.assertEqual(lease.leader_of([e("a", 1), e("b", 9, lt=old)], now), "a", "a stale lt holds nothing")
        self.assertEqual(lease.leader_of([e("a", 1), e("b", 9, leader=False)], now), "a", "leader: false holds nothing")
        self.assertIsNone(lease.leader_of([], now))

    def test_parse(self):
        self.assertIsNone(lease.parse("hello"))
        self.assertIsNone(lease.parse('{"machine": "a", "epoch": true}'))
        got = lease.parse(lease.worded("a", 3, True, None), stamp=T0)
        self.assertEqual((got["epoch"], got["leader"], got["lt"]), (3, True, T0), "leader: true: lt is the edit's time")
        got = lease.parse(lease.worded("a", 3, False, T0), stamp=T0 + datetime.timedelta(minutes=9))
        self.assertEqual(got["lt"], T0, "leader: false carries the old lt")


class Rounds(unittest.TestCase):
    def setUp(self):
        self.discord = Discord()
        self.discord.refuse = False
        self.ch = Chan(self.discord)
        self.clock = Clock()
        self.l = lease.Lease("host-a", ["host-a", "host-b"], lambda: self.ch, 1, clock_=self.clock)

    def test_holding_and_letting_go_by_time(self):
        self.assertFalse(self.l.ok(), "nothing read yet")
        self.assertEqual(asyncio.run(self.l.round()), "host-a")
        self.assertTrue(self.l.ok())
        self.clock.t += lease.READ_OK_SECS + 1
        self.assertFalse(self.l.ok(), "no good read for 90 s")
        self.assertFalse(self.l.giving_up())
        self.clock.t += lease.YIELD_SECS
        self.assertTrue(self.l.giving_up(), "false for 3 minutes in a row")

    def test_an_edit_refused_lets_go_in_two_minutes(self):
        asyncio.run(self.l.round())
        self.discord.refuse = True
        for _ in range(3):
            self.clock.t += 50
            self.discord.tick(50)
            asyncio.run(self.l.round())
        self.assertFalse(self.l.ok(), "its own edit refused 150 s: not the bot (#code-review: reads alone do not hold it)")

    def test_a_bigger_epoch_holding_it(self):
        self.ch.msgs.append(other(self.discord, "host-b", 5))
        self.ch.msgs[-1].edited_at = self.discord.now
        self.assertEqual(asyncio.run(self.l.round()), "host-b")
        self.assertEqual(self.l.other, "host-b")
        self.assertFalse(self.l.ok())

    def test_a_stale_bigger_epoch_does_not(self):
        self.ch.msgs.append(other(self.discord, "host-b", 5))
        self.discord.tick(lease.FRESH_SECS + 10)
        self.assertEqual(asyncio.run(self.l.round()), "host-a")
        self.assertIsNone(self.l.other)

    def test_keeps_its_own_epoch(self):
        self.ch.msgs.append(Msg(self.discord, lease.worded("host-a", 7, False, None)))
        asyncio.run(self.l.round())
        self.assertEqual(lease.parse(self.ch.msgs[0].content)["epoch"], 7, "not written over with 0")

    def test_a_clock_going_back(self):
        asyncio.run(self.l.round())
        self.assertTrue(self.l.ok())
        self.clock.t -= 5
        self.assertFalse(self.l.ok())

    def test_its_newest_message_is_its_time(self):
        """Two messages of its own (#code-review-2 on 6550957): the newest one's time is what freshness is read by."""
        self.ch.msgs.append(Msg(self.discord, lease.worded("host-a", 1, False, None)))
        self.discord.tick(lease.FRESH_SECS + 60)
        self.ch.msgs.insert(0, Msg(self.discord, lease.worded("host-a", 1, True, None)))  # (newest first, as Discord)
        self.ch.msgs[0].edited_at = self.discord.now
        self.ch.msgs.append(other(self.discord, "host-b", 9))
        self.ch.msgs[-1].edited_at = self.discord.now - datetime.timedelta(seconds=lease.FRESH_SECS + 30)
        self.assertEqual(asyncio.run(self.l.read()), "host-a", "host-b's old lt is stale against host-a's newest")

    def test_only_the_bots_own_messages(self):
        self.ch.msgs.append(Msg(self.discord, lease.worded("host-b", 99, True, None), author=2))
        self.ch.msgs[-1].edited_at = self.discord.now
        self.assertEqual(asyncio.run(self.l.round()), "host-a", "someone else's message is no lease")


class Restarts(unittest.TestCase):
    """STABLE_SECS (drill item 17, manager 11-09/11-10): a bot restarted while crashing writes leader: true only once
    up two minutes, and meanwhile is the bot only while its old lt is young enough; every other start writes at once."""
    def setUp(self):
        self.discord = Discord()
        self.discord.refuse = False
        self.ch = Chan(self.discord)
        self.clock = Clock()
        self.mark = os.path.join(tempfile.mkdtemp(), "lease-true-at")
        p = mock.patch.object(lease.time, "time", lambda: self.clock.t)  # (the wall clock true_at() checks with)
        p.start()
        self.addCleanup(p.stop)

    def bot(self, crashing=False, mark=True):
        return lease.Lease("host-a", ["host-a", "host-b"], lambda: self.ch, 1, clock_=self.clock, crashing=crashing,
                           mark=self.mark if mark else None)

    def own(self):
        m = next(m for m in self.ch.msgs if lease.parse(m.content)["machine"] == "host-a")
        return lease.parse(m.content, m.edited_at or m.created_at)

    def standby_sees(self):
        """What host-b's gate would do now (its own leader: false edit is now)."""
        b = {"machine": "host-b", "epoch": 1, "leader": False, "lt": None, "at": self.discord.now}
        return lease.decide("standby", "host-b", [self.own(), b], self.discord.now, ["host-a", "host-b"])

    def tick(self, secs):
        self.clock.t += secs
        self.discord.tick(secs)

    def never_both(self, l, secs, standby_edits=False):
        """`secs` of rounds every ROUND_SECS, looked at every 10 s: never this bot ok() while host-b would take over."""
        for t in range(0, secs, 10):
            if t % lease.ROUND_SECS == 0:
                if standby_edits:
                    self.standby_edit()
                asyncio.run(l.round())
            self.assertFalse(l.ok() and self.standby_sees() == "takeover",
                             "both the bot at %d s, lt %d s old" % (t, (self.discord.now - self.own()["lt"]).total_seconds()))
            self.tick(10)

    def standby_edit(self):
        m = next((m for m in self.ch.msgs if lease.parse(m.content)["machine"] == "host-b"), None)
        if m is None:
            self.ch.msgs.append(other(self.discord, "host-b", 1, leader=False))
        else:
            m.edited_at = self.discord.now

    def down_then_up(self, down, crashing, mark=True, standby_edits=False):
        l = self.bot()
        for _ in range(3):
            asyncio.run(l.round())
            self.tick(lease.ROUND_SECS)
        self.tick(30 - lease.ROUND_SECS)  # (the last edit 30 s before it stops)
        self.tick(down)
        if standby_edits:
            self.standby_edit()
        return self.bot(crashing=crashing, mark=mark)

    def test_a_start_without_the_place_writes_leader_true_at_once(self):
        l = self.bot(crashing=True)
        self.assertEqual(asyncio.run(l.round()), "host-a")
        self.assertTrue(self.own()["leader"])
        self.assertIs(l.restart, False)

    def test_a_restart_not_crashing_writes_leader_true_at_once(self):
        asyncio.run(self.bot().round())
        lt0 = self.own()["lt"]
        self.tick(5)
        asyncio.run(self.bot().round())
        self.assertGreater(self.own()["lt"], lt0, "a deploy, !restart: as before")

    def test_down_200_s_then_a_normal_restart_is_not_taken_over(self):
        """Manager 11-10: back to "not taken over for about 240 s"."""
        l = self.down_then_up(200, crashing=False)
        for _ in range(4):
            asyncio.run(l.round())
            self.assertTrue(l.ok())
            self.assertEqual(self.standby_sees(), "wait")
            self.tick(lease.ROUND_SECS)

    def test_down_200_s_then_a_crashing_restart_is_never_the_bot_with_the_standby(self):
        """#code-review and #code-review-2 on 67ba1d8: no edit, but not the bot once its lt is near stale."""
        l = self.down_then_up(200, crashing=True)
        lt0 = self.own()["lt"]
        self.never_both(l, lease.STABLE_SECS)
        self.assertFalse(l.ok(), "its lt 350 s old by now")
        self.assertEqual(self.own()["lt"], lt0, "and no edit before STABLE_SECS")

    def test_down_200_s_crashing_without_its_own_record_the_standbys_edits_say(self):
        l = self.down_then_up(200, crashing=True, mark=False, standby_edits=True)
        self.never_both(l, lease.STABLE_SECS, standby_edits=True)

    def test_down_200_s_crashing_by_its_record_when_no_machine_edits(self):
        """No other edit since its own: the lt looks new against them; this machine's own record says how old."""
        l = self.down_then_up(200, crashing=True)
        asyncio.run(l.round())
        self.assertIs(l.restart, True)
        self.assertFalse(l.ok(), "lt 230 s old (its record), over FRESH_SECS - SETTLE_MARGIN")

    def test_a_crashing_restart_makes_no_edit_for_two_minutes(self):
        asyncio.run(self.bot().round())
        lt0 = self.own()["lt"]
        self.tick(5)
        l = self.bot(crashing=True)  # (the bot restarted: a new Lease, the same message)
        self.assertEqual(asyncio.run(l.round()), "host-a")
        self.assertIs(l.restart, True)
        self.assertEqual(self.own()["lt"], lt0, "no edit: lt not moved")
        self.assertTrue(l.ok(), "still the bot meanwhile, by the old lt: nothing held back")
        self.tick(lease.ROUND_SECS)
        asyncio.run(l.round())
        self.assertEqual(self.own()["lt"], lt0)
        self.assertTrue(l.ok())
        self.tick(lease.ROUND_SECS)
        asyncio.run(l.round())
        self.assertTrue(self.own()["leader"], "up STABLE_SECS: leader: true")
        self.assertGreater(self.own()["lt"], lt0)

    def test_a_bot_crashing_every_half_minute_lets_the_place_go_after_fresh_secs(self):
        asyncio.run(self.bot().round())
        lt0 = self.own()["lt"]
        free_at = None
        for _ in range(30):  # (each life: up 30 s, two rounds, then the supervisor's 5 s)
            l = self.bot(crashing=True)
            asyncio.run(l.round())
            self.assertFalse(l.ok() and self.standby_sees() == "takeover")
            self.tick(30)
            asyncio.run(l.round())
            self.assertFalse(l.ok() and self.standby_sees() == "takeover")
            self.tick(5)
            if free_at is None and self.standby_sees() == "takeover":
                free_at = (self.discord.now - lt0).total_seconds()
        self.assertEqual(self.own()["lt"], lt0, "no start moved lt")
        self.assertIsNotNone(free_at)
        self.assertLessEqual(free_at, lease.FRESH_SECS + 35, "free as soon as FRESH_SECS has passed")

    def test_a_first_read_that_fails_writes_no_leader_true(self):
        asyncio.run(self.bot().round())
        lt0 = self.own()["lt"]
        self.tick(5)
        l = self.bot(crashing=True)
        with mock.patch.object(l, "read", side_effect=OSError("502")):
            asyncio.run(l.round())
        self.assertIsNone(l.restart)
        self.assertEqual(self.own()["lt"], lt0, "no edit")
        self.assertFalse(l.ok())
        asyncio.run(l.round())
        self.assertIs(l.restart, True, "the next round's read decides")
        self.assertEqual(self.own()["lt"], lt0)

    def test_its_lt_stale_by_another_machines_edit_is_no_restart(self):
        """Down longer than FRESH_SECS while the standby edits every minute: not holding, so leader: true at once."""
        asyncio.run(self.bot().round())
        self.tick(lease.FRESH_SECS + 60)
        self.ch.msgs.append(other(self.discord, "host-b", 1, leader=False))
        l = self.bot(crashing=True)
        asyncio.run(l.round())
        self.assertIs(l.restart, False)
        self.assertEqual(self.own()["lt"], self.discord.now, "leader: true written at once")

    def test_its_record_trusted_only_on_the_same_boot_with_the_wall_clock(self):
        lease.wrote_true(self.mark, 1000.0)  # (the wall clock is self.clock.t, 1000 now)
        self.clock.t += 100
        self.assertEqual(lease.true_at(self.mark, 1100.0), 1000.0)
        self.assertIsNone(lease.true_at(self.mark, 900.0), "in the future")
        self.clock.t += 500  # (the wall clock moved 600 s, this clock 100)
        self.assertIsNone(lease.true_at(self.mark, 1100.0))
        self.clock.t -= 500
        with mock.patch.object(lease, "boot_id", lambda: "another-boot"):
            self.assertIsNone(lease.true_at(self.mark, 1100.0), "another boot")
        self.assertIsNone(lease.true_at(self.mark + ".none", 1100.0))


class Bot(unittest.TestCase):
    """main.Bot: without a standby nothing of this runs (manager 11-08); with one, held back and let through."""

    def bot(self):
        from bot import main
        b = mock.Mock()
        b.lease, b.held_back, b.letting = None, [], set()
        b.leader_ok = lambda: main.Bot.leader_ok(b)
        b.hold = lambda m: main.Bot.hold(b, m)
        return main, b

    def test_without_a_standby_nothing_runs(self):
        main, b = self.bot()
        with mock.patch.object(main.remote, "inventory", lambda: {"machines": {"host-a": {"roles": ["dcbot"]}}}), \
                mock.patch.object(main.lease_mod, "Lease", side_effect=AssertionError("no Lease without a standby")):
            self.assertIsNone(main.Bot.start_lease(b, mock.Mock()))
        self.assertIsNone(b.lease)
        self.assertTrue(b.leader_ok())
        self.assertFalse(b.hold(mock.Mock()), "nothing held back")
        b.discord_ok = mock.Mock(side_effect=AssertionError("no discord-ok without a standby"))
        from bot import tmux_bridge
        self.assertTrue(tmux_bridge.leading(b))
        # the heartbeat loop too (#code-review on 4dc8fe8): a round with the gateway up writes no discord-ok
        import asyncio
        b.gateway_up = lambda: True

        async def one_round(secs):
            raise asyncio.CancelledError
        with mock.patch.object(main, "STATE", tempfile.mkdtemp()), mock.patch.object(main.asyncio, "sleep", one_round):
            with self.assertRaises(asyncio.CancelledError):
                asyncio.run(main.Bot.heartbeat(b))
        b.discord_ok.assert_not_called()

    def test_with_a_standby_a_lease_round_runs(self):
        main, b = self.bot()
        b.user.id = 1
        b.loop.create_task = lambda coro: coro.close()
        with mock.patch.object(main.remote, "inventory", lambda: {"machines": {"host-a": {"roles": ["dcbot"]}, "host-b": {"roles": [lease.ROLE]}}}), \
                mock.patch.object(main.remote, "this_machine", lambda: "host-a"), \
                mock.patch.object(main.remote, "LEASED", False):  # (start_lease sets it: not left for the next tests)
            main.Bot.start_lease(b, mock.Mock(text_channels=[]))
            self.assertTrue(main.remote.LEASED, "requests wait for the first round's epoch from here (remote.run)")
        self.assertIsInstance(b.lease, lease.Lease)

    def test_held_back_then_let_through_in_order(self):
        main, b = self.bot()
        b.lease = mock.Mock()
        b.lease.ok.return_value = False
        got = []

        async def on_message(m):
            if not b.hold(m):
                got.append(m)
        b.on_message = on_message
        for m in ("one", "two"):
            asyncio.run(on_message(m))
        self.assertEqual((got, [m for _, m in b.held_back]), ([], ["one", "two"]))
        b.lease.ok.return_value = True
        asyncio.run(main.Bot.let_through(b))
        self.assertEqual(got, ["one", "two"])

    def test_one_arriving_while_let_through_waits_behind(self):
        """Only the messages being let through skip hold() (both reviews on 6550957); not the bot again halfway:
        what is left waits again, in its order."""
        main, b = self.bot()
        b.lease = mock.Mock()
        b.lease.ok.return_value = False
        got = []

        async def on_message(m):
            if not b.hold(m):
                got.append(m)
                if m == "one":
                    b.lease.ok.return_value = False  # (lost again while "one" was handled)
                    await on_message("new")
        b.on_message = on_message
        for m in ("one", "two"):
            asyncio.run(on_message(m))
        b.lease.ok.return_value = True
        asyncio.run(main.Bot.let_through(b))
        self.assertEqual(got, ["one"])
        self.assertEqual([m for _, m in b.held_back], ["two", "new"], "the old first, then the new one")

    def test_a_lease_task_that_ends_ends_the_bot(self):
        main, b = self.bot()
        b.lease = mock.Mock()
        b.lease_over = False
        b.is_closed.return_value = False
        b.loop.create_task = lambda coro: coro.close()
        task = mock.Mock()
        task.cancelled.return_value = False
        task.exception.return_value = RuntimeError("boom")
        main.Bot.lease_ended(b, task)
        self.assertEqual(b.exit_code, 3)

    def test_buttons_answered_while_not_the_bot(self):
        main, b = self.bot()
        b.lease = mock.Mock()
        b.lease.ok.return_value = False
        it = mock.Mock()
        it.type = main.discord.InteractionType.component
        it.response.send_message = mock.AsyncMock()
        asyncio.run(main.Bot.on_interaction(b, it))
        it.response.send_message.assert_awaited_once_with(messages.lease("busy"), ephemeral=True)

    def test_slash_commands_are_left_to_slash_begin(self):
        """#code-review on 6550957: a slash command or an autocomplete is not answered here (slash.begin() does)."""
        main, b = self.bot()
        b.lease = mock.Mock()
        b.lease.ok.return_value = False
        for kind in (main.discord.InteractionType.application_command, main.discord.InteractionType.autocomplete):
            it = mock.Mock()
            it.type = kind
            it.data = {}
            it.response.send_message = mock.AsyncMock()
            asyncio.run(main.Bot.on_interaction(b, it))
            it.response.send_message.assert_not_called()

    def test_the_spool_waits_while_not_the_bot(self):
        from bot import tmux_bridge
        bridge = tmux_bridge.TmuxBridge.__new__(tmux_bridge.TmuxBridge)
        bridge.host = mock.Mock(leader_ok=lambda: False)
        with mock.patch.object(tmux_bridge.events, "waiting", side_effect=AssertionError("the spool is not read")):
            asyncio.run(bridge.pump_round())

    def test_the_heartbeat_only_while_the_bot(self):
        import os
        import tempfile
        main, b = self.bot()
        b.lease = mock.Mock()
        b.lease.ok.return_value = False
        state = tempfile.mkdtemp()

        async def go():
            with mock.patch.object(main, "STATE", state), mock.patch.object(main.asyncio, "sleep", side_effect=asyncio.CancelledError):
                try:
                    await main.Bot.heartbeat(b)
                except asyncio.CancelledError:
                    pass
        asyncio.run(go())
        self.assertFalse(os.path.exists(os.path.join(state, "heartbeat")))


if __name__ == "__main__":
    unittest.main()


class DownMark(unittest.TestCase):
    """Drill item 20: the primary's "down" in its message (lease.worded / parse)."""

    def test_written_only_when_given(self):
        self.assertNotIn("down", json.loads(lease.worded("host-a", 1, False, None)), "the old format as it was")
        self.assertEqual(json.loads(lease.worded("host-a", 1, False, None, "crashing"))["down"], "crashing")

    def test_read_from_both_formats(self):
        self.assertIsNone(lease.parse('{"machine": "host-a", "epoch": 1, "leader": false, "lt": null}')["down"])
        self.assertEqual(lease.parse(lease.worded("host-a", 1, False, None, "crashing"))["down"], "crashing")
        for odd in ('""', "1", "true", "null", "[]"):
            self.assertIsNone(lease.parse('{"machine": "host-a", "epoch": 1, "down": %s}' % odd)["down"], odd)


class GiveUpEndsNow(unittest.TestCase):
    """The drill's 16:11 (manager 11-10): the place given up while discord.py's reconnect loop slept 930 s; the process
    ended 20 minutes later. give_up() now cancels the start task (main.run_bot) and arms an os._exit backstop."""

    def fake(self):
        from unittest import mock
        from bot import main

        class Fake(object):
            lease_over, exit_code, held_back, closed = False, 0, [1], False

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                await self.close()

            async def start(self, token):
                await asyncio.sleep(930)  # (discord.py's Client.connect(), asleep before its next reconnect)

            async def close(self):
                self.closed = True

        f = Fake()
        f.lease = mock.Mock(write=mock.AsyncMock(side_effect=OSError("drill: Discord out of reach")))
        f.end_now = lambda: main.Bot.end_now(f)
        f.arm_end = lambda: main.Bot.arm_end(f)
        f.audit = mock.AsyncMock()
        return f

    def test_a_fenced_bot_ends_at_once_too(self):
        """#code-review on ac4bed6 (manager): fenced_out() as give_up(): a reconnect asleep does not keep it."""
        import time
        from unittest import mock
        from bot import main
        f, timers = self.fake(), []

        async def go():
            run = asyncio.ensure_future(main.run_bot(f, "token"))
            await asyncio.sleep(0.05)
            f.lease_over = True  # (as lease_round sets it before the task: main.Bot's fence check)
            await main.Bot.fenced_out(f, 9)
            await asyncio.wait_for(run, 5)
        with mock.patch.object(main.threading, "Timer", lambda secs, fn: timers.append((secs, fn)) or mock.Mock()), \
                mock.patch.object(main, "stepped_down_note", lambda b: ""):
            t0 = time.monotonic()
            asyncio.run(go())
        self.assertLess(time.monotonic() - t0, 5)
        self.assertEqual((f.exit_code, f.closed, len(timers)), (4, True, 1))

    def test_a_close_that_hangs_still_ends_by_the_backstop(self):
        """#code-review-2 on ac4bed6 (manager): the backstop is armed before close(), so a close() that never
        returns still ends the process (os._exit(4)) END_SECS later."""
        import contextlib
        import io
        from unittest import mock
        from bot import main
        f, timers = self.fake(), []

        async def hang():
            await asyncio.sleep(3600)
        f.close = hang

        async def go():
            with contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(asyncio.TimeoutError):
                    await asyncio.wait_for(main.Bot.give_up(f), 0.2)
        with mock.patch.object(main.threading, "Timer", lambda secs, fn: timers.append((secs, fn)) or mock.Mock()):
            asyncio.run(go())
        self.assertEqual([s for s, _ in timers], [main.END_SECS], "armed while close() still hangs")
        with mock.patch.object(main.os, "_exit") as hard, contextlib.redirect_stdout(io.StringIO()):
            timers[0][1]()
        hard.assert_called_once_with(4)

    def test_a_long_reconnect_sleep_does_not_keep_the_process(self):
        import contextlib
        import io
        import time
        from unittest import mock
        from bot import main
        f, timers = self.fake(), []

        async def go():
            run = asyncio.ensure_future(main.run_bot(f, "token"))
            await asyncio.sleep(0.05)
            with contextlib.redirect_stderr(io.StringIO()):  # (the failed leader: false write's traceback)
                await main.Bot.give_up(f)
            await asyncio.wait_for(run, 5)
        with mock.patch.object(main.threading, "Timer", lambda secs, fn: timers.append((secs, fn)) or mock.Mock()):
            t0 = time.monotonic()
            asyncio.run(go())
        self.assertLess(time.monotonic() - t0, 5, "ended at once, not after the reconnect's sleep")
        self.assertEqual((f.exit_code, f.closed, f.held_back), (4, True, []))
        self.assertEqual([s for s, _ in timers], [main.END_SECS], "the backstop armed")
        with mock.patch.object(main.os, "_exit") as hard:
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                timers[0][1]()
        hard.assert_called_once_with(4)
        self.assertIn("still running", out.getvalue())

    def test_a_cancel_not_from_give_up_is_not_swallowed(self):
        from bot import main
        f = self.fake()

        async def go():
            run = asyncio.ensure_future(main.run_bot(f, "token"))
            await asyncio.sleep(0.05)
            f.start_task.cancel()
            await run
        with self.assertRaises(asyncio.CancelledError):
            asyncio.run(go())
