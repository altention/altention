"""A channel deleted while its Claude runs on (#issues 21, stage 3b step 2.2e): what it still asks is let go at once,
and what it asks afterwards is refused at once. Before, the hook (`dchook --ask`) waited for an answer nobody could
give, up to its limit of half a day, and the conversation under it stood still."""
import asyncio
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

from bot import ask, config, tmux_bridge
from tests import tmuxguard  # noqa: F401,E402  (no test reaches a real tmux server)
from tests.test_ask import FakeChannel, Q1
from tests.test_turn_end import Ch, Host

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class World(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        saved = (ask.ASKS, ask.ANSWERS, config.STATE)
        self.addCleanup(lambda: (setattr(ask, "ASKS", saved[0]), setattr(ask, "ANSWERS", saved[1]), setattr(config, "STATE", saved[2])))
        self.state = os.path.join(self.tmp.name, "state")
        ask.ASKS, ask.ANSWERS, config.STATE = os.path.join(self.state, "asks"), os.path.join(self.state, "answers"), self.state

    def answer(self, ask_id):
        try:
            with open(os.path.join(ask.ANSWERS, ask_id + ".json")) as f:
                return json.load(f)
        except OSError:
            return None


class OpenWhenDeleted(World):
    def setUp(self):
        super().setUp()
        self.done = []

        async def audit(text):
            pass

        async def on_done(ch, ask_id, how):
            self.done.append((ask_id, how))
        self.asker, self.ch = ask.Asker(audit), FakeChannel()
        self.asker.on_done = on_done

    def test_an_open_question_and_an_open_plan_are_let_go(self):
        async def go():
            await self.asker.post(self.ch, {"ask_id": "5-1", "questions": [Q1]})
            await self.asker.post_plan(self.ch, {"ask_id": "5-2", "plan": "the plan"})
            other = FakeChannel()
            other.id = 10
            await self.asker.post(other, {"ask_id": "6-1", "questions": [Q1]})
            before = [m.content for m in self.ch.sent]
            await self.asker.gone(self.ch)
            return before, [m.content for m in self.ch.sent]
        before, after = asyncio.run(go())
        self.assertEqual(self.answer("5-1"), {"reason": ask.GONE})
        self.assertEqual(self.answer("5-2"), {}, "a plan: left to the terminal's own dialog")
        self.assertIsNone(self.answer("6-1"), "another channel's question stays")
        self.assertEqual([i for i, s in ask.waiting()], ["6-1"])
        self.assertEqual(before, after, "nothing is edited or posted: the messages went with the channel")
        self.assertEqual(sorted(self.done), [("5-1", "已取消"), ("5-2", "已取消")], "a notice of it in another channel is updated")

    def test_a_notice_that_cannot_be_updated_does_not_keep_the_next_question_waiting(self):
        async def broken(ch, ask_id, how):
            raise RuntimeError("the other channel is gone too")
        self.asker.on_done = broken

        async def go():
            await self.asker.post(self.ch, {"ask_id": "5-1", "questions": [Q1]})
            await self.asker.post(self.ch, {"ask_id": "5-2", "questions": [Q1]})
            with mock.patch.object(ask.traceback, "print_exc"):
                await self.asker.gone(self.ch)
        asyncio.run(go())
        self.assertEqual((self.answer("5-1"), self.answer("5-2"), ask.waiting()), ({"reason": ask.GONE}, {"reason": ask.GONE}, []))

    def test_one_on_another_machine_is_answered_over_there(self):
        sent = []

        async def deliver_to(host, ask_id, result):
            sent.append((host, ask_id, result))

        async def go():
            await self.asker.post(self.ch, {"ask_id": "5-1", "questions": [Q1], "host": "lap"})
            with mock.patch.object(ask, "deliver_to", deliver_to):
                await self.asker.gone(self.ch)
                at_once = list(sent)
                await self.asker.again("lap")
            return at_once
        self.assertEqual(asyncio.run(go()), [], "never waited for where the channel is let go: it is owed")
        self.assertEqual(sent, [("lap", "5-1", {"reason": ask.GONE})])

    def test_the_bot_lets_them_go_when_a_channel_is_deleted(self):
        from bot import main as botmain
        with mock.patch.object(botmain.Bot, "save", lambda self: None):
            bot = botmain.Bot()
        audits = []

        async def audit(text):
            audits.append(text)

        async def unbind(*a, **k):
            pass

        async def go():
            await bot.tmux.asks.post(self.ch, {"ask_id": "5-1", "questions": [Q1]})
            bot.channels_state[str(self.ch.id)] = {"tmux": "dc-c-1", "cwd": "/w"}
            with mock.patch.object(bot, "audit", audit), mock.patch.object(bot, "save", lambda: None), \
                    mock.patch.object(botmain.routes, "unbind_any", unbind):
                await bot.on_guild_channel_delete(self.ch)
        asyncio.run(go())
        self.assertEqual((self.answer("5-1"), ask.waiting()), ({"reason": ask.GONE}, []))
        self.assertNotIn(str(self.ch.id), bot.channels_state)
        self.assertEqual(len(audits), 1, "and #audit says, as ever, where its Claude still runs")


class AskedAfterwards(World):
    def handle(self, ev, found=None):
        async def go():
            host = Host(found)
            host.get_channel = lambda cid: found
            bridge = tmux_bridge.TmuxBridge(host)
            bridge.last_reap = 1e12
            await bridge.handle(dict(ev, channel="424242"))
            return host
        return asyncio.run(go())

    def test_a_question_of_a_channel_that_is_no_more_is_refused_at_once_and_a_plan_handed_back(self):
        self.handle({"hook_event_name": "Ask", "ask_id": "7-1", "questions": [Q1], "session_id": "s1"})
        self.handle({"hook_event_name": "Plan", "ask_id": "7-2", "plan": "the plan", "session_id": "s1"})
        self.handle({"hook_event_name": "Ask", "session_id": "s1"})  # (no id: no hook waits)
        self.handle({"hook_event_name": "Stop", "session_id": "s1", "last_assistant_message": "x"})
        self.assertEqual((self.answer("7-1"), self.answer("7-2")), ({"reason": ask.GONE}, {}))
        self.assertEqual((sorted(os.listdir(ask.ANSWERS)), ask.waiting()), (["7-1.json", "7-2.json"], []))

    def test_one_from_another_machine_is_answered_over_there(self):
        sent = []

        async def deliver_to(host, ask_id, result):
            sent.append((host, ask_id, result))
        with mock.patch.object(ask, "deliver_to", deliver_to):
            self.handle({"hook_event_name": "Ask", "ask_id": "7-1", "questions": [Q1], "host": "lap"})
        self.assertEqual(sent, [("lap", "7-1", {"reason": ask.GONE})])

    def test_the_real_hook_is_let_go_within_seconds(self):
        """`dchook --ask`, run as Claude Code runs it, for a channel the bot cannot find: the bot's handle() takes its
        event from the spool; the hook ends with the refusal Claude reads."""
        root = self.tmp.name
        proc = subprocess.Popen([sys.executable, os.path.join(ROOT, "tools", "dchook"), "--ask"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, env=dict(os.environ, DCBOT_ROOT=root, DCBOT_CHANNEL="424242", DCBOT_ENV="/nonexistent"))

        def pump():
            spool = os.path.join(root, "state", "events")
            for _ in range(200):
                names = [n for n in (os.listdir(spool) if os.path.isdir(spool) else []) if n.endswith(".json")]
                if names:
                    with open(os.path.join(spool, names[0])) as f:
                        ev = json.load(f)
                    return self.handle(ev)
                time.sleep(0.05)
        t0 = time.time()
        t = threading.Thread(target=pump)
        t.start()
        try:
            out, err = proc.communicate(json.dumps({"hook_event_name": "PreToolUse", "tool_name": "AskUserQuestion", "session_id": "s",
                                                    "tool_input": {"questions": [Q1]}}).encode(), 30)
        finally:
            proc.kill()
        t.join()
        self.assertEqual((proc.returncode, err), (0, b""))
        got = json.loads(out.decode())["hookSpecificOutput"]
        self.assertEqual(got["permissionDecision"], "deny")
        self.assertIn("channel was deleted", got["permissionDecisionReason"])
        self.assertLess(time.time() - t0, 15)



async def settle():
    """Let the tasks started just now run to their end (they wait on nothing here)."""
    for _ in range(20):
        await asyncio.sleep(0)


class NotToBeHadJustNow(World):
    """A channel Discord does not hand out for a while (reconnecting, not in the cache yet) is not a deleted one: its
    state is here. Nothing is known to be gone, so nothing is refused: its events are kept, in order, and handled
    when it is there again. (Before step 2.2e they were dropped, and a question among them waited out its limit.)"""

    def setUp(self):
        super().setUp()
        self.ch = Ch()
        self.there = False
        self.host = Host(self.ch)
        self.host.get_channel = lambda cid: self.ch if self.there and cid == self.ch.id else None
        self.host.chan(self.ch).update({"tmux": "dc-x-1", "cwd": "/w"})
        self.bridge = tmux_bridge.TmuxBridge(self.host)
        self.bridge.last_reap = 1e12
        self.shown = []

        async def post(c, ev):
            self.shown.append("question %s" % ev["ask_id"])
            return True
        for patch in (mock.patch.object(self.bridge, "watch", lambda *a, **k: None), mock.patch.object(self.bridge.asks, "post", post),
                      mock.patch.object(ask, "load", lambda ask_id: None), mock.patch("builtins.print", lambda *a, **k: self.lines.append(a[0]))):
            patch.start()
            self.addCleanup(patch.stop)
        self.lines = []

    def ev(self, name, **kw):
        return dict({"hook_event_name": name, "channel": str(self.ch.id), "session_id": "s1", "prompt_id": "a"}, **kw)

    def run_(self, coro):
        return asyncio.run(coro)

    def test_a_question_asked_meanwhile_is_shown_when_the_channel_is_back(self):
        async def go():
            await self.bridge.handle(self.ev("UserPromptSubmit", prompt="go"))
            await self.bridge.handle(self.ev("Ask", ask_id="8-1", questions=[Q1]))
            away = (list(self.shown), self.answer("8-1"), len(self.ch.sent), len(self.bridge.kept[str(self.ch.id)][1]))
            await self.bridge.release_kept()
            still = (list(self.shown), len(self.bridge.kept[str(self.ch.id)][1]))
            self.there = True
            await self.bridge.release_kept()
            await settle()
            return away, still
        away, still = self.run_(go())
        self.assertEqual(away, ([], None, 0, 2), "nothing shown, nothing refused, both kept")
        self.assertEqual(still, ([], 2), "still away: still kept")
        self.assertEqual((self.shown, self.answer("8-1"), self.bridge.kept), (["question 8-1"], None, {}), "shown with its buttons; never refused")
        self.assertEqual(len(self.lines), 2, self.lines)
        self.assertIn("not to be had from Discord just now", self.lines[0])
        self.assertIn("2 kept events were handled", self.lines[1])

    def test_the_next_event_of_a_channel_that_is_back_goes_behind_the_ones_kept(self):
        async def go():
            await self.bridge.handle(self.ev("UserPromptSubmit", prompt="go"))
            await self.bridge.handle(self.ev("PreToolUse", tool_name="Bash", tool_use_id="t1"))
            self.there = True
            await self.bridge.handle(self.ev("Stop", last_assistant_message="done."))
        self.run_(go())
        self.assertEqual(self.ch.sent[-1].content, "done.\n-# Bash×1 · 0 秒", "the turn and its tool were handled first, in order")
        self.assertEqual(self.bridge.kept, {})

    def test_deleted_meanwhile_the_question_kept_is_refused_then(self):
        async def go():
            await self.bridge.handle(self.ev("Ask", ask_id="8-2", questions=[Q1]))
            self.host.channels_state.pop(str(self.ch.id))  # on_guild_channel_delete: now it is known to be gone
            await self.bridge.release_kept()
            await settle()
        self.run_(go())
        self.assertEqual((self.shown, self.answer("8-2"), self.bridge.kept), ([], {"reason": ask.GONE}, {}))

    def test_kept_too_long_they_are_dropped_with_a_line_and_nothing_is_refused(self):
        async def go():
            await self.bridge.handle(self.ev("Ask", ask_id="8-3", questions=[Q1]))
            await self.bridge.handle(self.ev("Stop", last_assistant_message="x"))
            self.bridge.kept[str(self.ch.id)][0] -= tmux_bridge.KEPT_SECS + 1
            await self.bridge.release_kept()
            away = self.bridge.away_long()
            await self.bridge.handle(self.ev("Stop", last_assistant_message="y"))  # it goes on sending events
            self.bridge.kept[str(self.ch.id)][0] -= tmux_bridge.KEPT_SECS + 1
            await self.bridge.release_kept()
            self.assertEqual(len([line for line in self.lines if "not to be had" in line]), 2, "kept, and dropped: said once each for a channel")
            self.assertEqual([(a["channel"], a["events"]) for a in away], [(str(self.ch.id), 2)], "and written down")
            self.assertIn("正在用的頻道也會這樣", away[0]["why"])
            with open(os.path.join(self.state, tmux_bridge.UNREACHABLE)) as f:
                noted = json.load(f)["channels"]
            self.assertEqual([(n["channel"], n["events"]) for n in noted], [(str(self.ch.id), 3)], "in a file of its own, with what was dropped since")
            self.assertFalse(os.path.exists(os.path.join(self.state, tmux_bridge.OWNERLESS)), "not among the sessions that may be closed")
            self.there = True
            await self.bridge.release_kept()
            await settle()
            self.assertEqual(self.bridge.away_long(), [], "there again: off the list")
            with open(os.path.join(self.state, tmux_bridge.UNREACHABLE)) as f:
                self.assertEqual(json.load(f)["channels"], [], "and the file says so")
        self.run_(go())
        self.assertEqual((self.shown, self.answer("8-3"), self.bridge.kept, self.ch.sent), ([], None, {}, []), "as before this step: the hook is left to its own limit")
        self.assertIn("its 2 kept events are dropped (Ask, Stop)", self.lines[1])

    def test_no_more_than_the_limit_is_kept_for_a_channel(self):
        async def go():
            with mock.patch.object(tmux_bridge, "KEPT_MAX", 3):
                for n in range(5):
                    await self.bridge.handle(self.ev("PreToolUse", tool_name="Bash", tool_use_id="t%d" % n))
        self.run_(go())
        self.assertEqual([e["tool_use_id"] for e in self.bridge.kept[str(self.ch.id)][1]], ["t2", "t3", "t4"])
        self.assertEqual(len([line for line in self.lines if "the oldest is dropped" in line]), 2)

    def test_an_event_that_comes_while_the_kept_ones_are_handled_goes_behind_them(self):
        """The kept ones are handled by a task of their own; another machine's stream (another task) may bring the
        channel's next event in the middle. It must not overtake, and no two of a channel are handled at once."""
        order, inside, most = [], [0], [0]
        gate = asyncio.Event if False else None
        real = tmux_bridge.TmuxBridge.say

        async def go():
            hold = asyncio.Event()

            async def slow_say(bridge, ch, text, **kw):
                inside[0] += 1
                most[0] = max(most[0], inside[0])
                order.append(text.split("\n")[0])
                if text.startswith("first"):
                    await hold.wait()  # the first reply is still being posted
                inside[0] -= 1
                return []
            with mock.patch.object(tmux_bridge.TmuxBridge, "say", slow_say):
                await self.bridge.handle(self.ev("Stop", last_assistant_message="first", prompt_id="a"))
                await self.bridge.handle(self.ev("Stop", last_assistant_message="second", prompt_id="b"))
                self.there = True
                await self.bridge.release_kept()
                await settle()  # the release is under way, inside the first
                stream = asyncio.ensure_future(self.bridge.handle(self.ev("Stop", last_assistant_message="third", prompt_id="c")))
                pump = asyncio.ensure_future(self.bridge.release_kept())  # (and the round looks again meanwhile)
                await settle()
                midway = list(order)
                hold.set()
                await asyncio.gather(stream, pump)
                await settle()
            return midway
        midway = self.run_(go())
        self.assertEqual(midway, ["first"], "nothing overtook the one being handled")
        self.assertEqual((order, most[0], self.bridge.kept), (["first", "second", "third"], 1, {}))
        self.assertIsNotNone(real)

    def test_a_channel_away_again_midway_keeps_the_rest_in_its_order(self):
        async def go():
            for n in range(3):
                await self.bridge.handle(self.ev("Stop", last_assistant_message="r%d" % n, prompt_id="p%d" % n))
            self.there = True
            real = self.bridge.handle

            async def handle(ev, kept=False):
                await real(ev, kept=kept)
                if ev.get("last_assistant_message") == "r0":
                    self.there = False  # gone again after the first was handled
            with mock.patch.object(self.bridge, "handle", handle):
                await self.bridge.release(str(self.ch.id))
            left = [e["last_assistant_message"] for e in self.bridge.kept[str(self.ch.id)][1]]
            self.there = True
            await self.bridge.release(str(self.ch.id))
            return left
        self.assertEqual(self.run_(go()), ["r1", "r2"])
        self.assertEqual([m.content.split("\n")[0] for m in self.ch.sent], ["r0", "r1", "r2"])

    def test_another_channels_events_are_handled_as_ever(self):
        other = Ch()
        other.id = self.ch.id + 1
        self.host.get_channel = lambda cid: other if cid == other.id else None
        self.host.channels_state[str(other.id)] = {"tmux": "dc-y-1", "cwd": "/w"}

        async def go():
            await self.bridge.handle(self.ev("Stop", last_assistant_message="kept"))
            await self.bridge.handle(dict(self.ev("Stop", last_assistant_message="posted"), channel=str(other.id)))
        self.run_(go())
        self.assertEqual(([m.content for m in other.sent], list(self.bridge.kept)), (["posted"], [str(self.ch.id)]))


class AMachineThatDoesNotAnswer(World):
    def test_a_question_let_go_stays_on_record_and_is_sent_again(self):
        """The channel is deleted while the machine its question waits on is away: the refusal cannot be handed over.
        The record stays, marked as owed, open to nobody, and it is sent until it gets there (the hook waited out its
        limit before)."""
        async def audit(text):
            pass
        asker, ch, reach, sent, kicked = ask.Asker(audit), FakeChannel(), [False], [], []
        asker.on_owed = lambda: kicked.append(1)

        async def deliver_to(host, ask_id, result):
            sent.append((host, ask_id, result))
            return reach[0] if host else True  # (this machine's hook is always handed its answer)

        async def go():
            await asker.post(ch, {"ask_id": "9-1", "questions": [Q1], "host": "lap"})
            await asker.post(ch, {"ask_id": "9-2", "questions": [Q1]})
            with mock.patch.object(ask, "deliver_to", deliver_to):
                await asker.gone(ch)
                first = ([(i, s.get("owed")) for i, s in ask.waiting(owed=True)], ask.waiting(), ask.load("9-1"), list(sent), len(kicked))
                await asker.gone(ch)  # (told twice: not let go twice)
                left = [await asker.again("lap"), await asker.again("other")]
                second = [i for i, s in ask.waiting(owed=True)]
                reach[0] = True
                left += [await asker.again("lap"), await asker.again("lap")]
            return first, second, left
        first, second, left = asyncio.run(go())
        self.assertEqual(first, ([("9-1", {"reason": ask.GONE})], [], None, [(None, "9-2", {"reason": ask.GONE})], 1),
                         "the local one handed over at once; the other machine's owed, open to nobody, not waited for, its task asked for")
        self.assertEqual(left, [1, 0, 0, 0], "how many are still owed to that machine; another machine's are not its")
        self.assertEqual(second, ["9-1"])
        self.assertEqual((ask.waiting(), ask.waiting(owed=True)), ([], []))
        self.assertEqual([s[:2] for s in sent], [(None, "9-2"), ("lap", "9-1"), ("lap", "9-1")], "sent until it got there, and no more")

    def test_it_is_given_up_after_the_hooks_own_limit(self):
        async def audit(text):
            pass
        asker, ch = ask.Asker(audit), FakeChannel()

        async def go():
            await asker.post(ch, {"ask_id": "9-3", "questions": [Q1], "host": "lap"})
            with mock.patch.object(ask, "deliver_to", self.never):
                await asker.gone(ch)
                await asker.again("lap")
                kept = (len(ask.waiting(owed=True)), ask.undelivered())
                await asker.again("lap", limit=-1)
            return kept
        self.assertEqual((asyncio.run(go()), ask.waiting(owed=True), ask.undelivered()), ((1, ["lap"]), [], []))

    def bridge_with_records(self, n=3):
        async def audit(text):
            pass
        ch = Ch()
        bridge = tmux_bridge.TmuxBridge(Host(ch))
        bridge.last_reap = 1e12
        bridge.asks.on_owed = None  # (the tests start the machine's task themselves)
        fake = FakeChannel()

        async def make():
            for i in range(n):
                await bridge.asks.post(fake, {"ask_id": "9-%d" % i, "questions": [Q1], "host": "lap"})
            await bridge.asks.gone(fake)  # (owed: nothing is sent from here)
        return bridge, ch, make

    @staticmethod
    async def never(host, ask_id, result):
        return False

    def test_the_bots_round_does_not_wait_for_a_machine_that_does_not_answer(self):
        """Three refusals owed to a machine whose every command takes 3 s to fail: the round of events returns at
        once and this machine's events are handled as ever (the manager's rule: nothing in pump_once() waits on
        another machine; code-review measured 9 s a round)."""
        from bot import remote
        bridge, ch, make = self.bridge_with_records(3)
        spool = os.path.join(self.state, "events")
        os.makedirs(spool)
        calls = []

        async def dcremote(host, *args, **kw):
            calls.append(args[:2])
            await asyncio.sleep(3)
            return 255, ""

        async def go():
            await make()
            with open(os.path.join(spool, "0001.json"), "w") as f:
                json.dump({"hook_event_name": "Stop", "channel": str(ch.id), "session_id": "s1", "last_assistant_message": "here.", "ts": time.time()}, f)
            with mock.patch.object(remote, "dcremote", dcremote), mock.patch.object(tmux_bridge, "SPOOL", spool), mock.patch.object(bridge, "start_streams", lambda: None), \
                    mock.patch.object(remote, "problem", None):
                bridge.streams_checked = time.time()
                t0 = time.time()
                await bridge.pump_once()
                took = time.time() - t0
                await asyncio.sleep(0.05)
                started = (list(calls), sorted(bridge.resending))
                for task in [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]:
                    task.cancel()
            return took, started
        took, started = asyncio.run(go())
        self.assertLess(took, 0.1)
        self.assertEqual(ch.sent[-1].content, "here.")
        self.assertEqual(started, ([("answer", "9-0")], ["lap"]), "one task for the machine, at its first record; the round did not wait for it")
        self.assertEqual(len(ask.waiting(owed=True)), 3)

    def test_one_task_for_a_machine_longer_waits_each_time_and_at_once_when_it_is_back(self):
        bridge, ch, make = self.bridge_with_records(2)
        tried, waits, reach = [], [], [False]

        async def deliver_to(host, ask_id, result):
            tried.append(ask_id)
            return reach[0]
        real_wait_for = asyncio.wait_for

        async def wait_for(aw, timeout):
            waits.append(timeout)
            if len(waits) == 6:
                reach[0] = True
                bridge.reached("lap")  # a command got through to it: tried at once
            if len(waits) >= 8:
                raise AssertionError("still waiting")
            try:
                return await real_wait_for(aw, 0.01)
            except asyncio.TimeoutError:
                raise

        async def go():
            await make()
            with mock.patch.object(ask, "deliver_to", deliver_to), mock.patch.object(tmux_bridge.asyncio, "wait_for", wait_for):
                bridge.start_resends()
                bridge.start_resends()  # (again while one runs: no second task)
                tasks = len([t for t in asyncio.all_tasks() if t is not asyncio.current_task()])
                for _ in range(200):
                    await asyncio.sleep(0.005)
                    if not bridge.resending:
                        break
            return tasks
        tasks = asyncio.run(go())
        self.assertEqual(tasks, 1)
        self.assertEqual(waits, [30, 60, 120, 240, 480, 600], "doubled up to the limit; then the machine answered")
        self.assertEqual((ask.waiting(owed=True), bridge.resending), ([], {}), "handed over, and the task is over")
        self.assertEqual(tried[-2:], ["9-0", "9-1"])

    def test_a_turns_end_and_the_reaper_do_not_wait_for_another_machines_hook(self):
        """A question on record for another machine (the channel followed a pane over there when it was asked), that
        machine away, and then: an event of this machine ends the turn (the open questions are cancelled), or the
        reaper closes a session of this machine whose channel is gone. Both ran in the bot's round and waited for
        the other machine; now the result is owed, the round goes on, and the machine's task hands it over when the
        machine is back."""
        from bot import remote
        ch = Ch()
        bridge = tmux_bridge.TmuxBridge(Host(ch))
        bridge.last_reap = 1e12
        calls, reach = [], [False]

        async def dcremote(host, *args, **kw):
            calls.append((host,) + args[:2])
            if not reach[0]:
                await asyncio.sleep(3)
                return 255, ""
            return 0, ""

        async def go():
            st = bridge.host.chan(ch)
            st.update({"tmux": "dc-x-1", "cwd": "/w"})
            for ask_id in ("7-1", "7-2"):
                ask.save(ask_id, {"channel": str(ch.id), "questions": [Q1], "answers": {}, "messages": {}, "ts": time.time(), "host": "lap"})
            ask.save("7-3", {"channel": "777", "questions": [Q1], "answers": {}, "messages": {}, "ts": time.time(), "host": "lap"})
            with mock.patch.object(remote, "dcremote", dcremote), mock.patch.object(bridge, "watch", lambda *a, **k: None):
                t0 = time.time()
                await bridge.handle({"hook_event_name": "Stop", "channel": str(ch.id), "session_id": "s1", "last_assistant_message": "done."})
                stop_took = time.time() - t0
                t0 = time.time()
                for ask_id, asked in ask.waiting("777"):  # what reap_ownerless() does with a closed session's questions
                    await bridge.asks.hand(ask_id, asked, {"reason": "closed"})
                reap_took = time.time() - t0
                await asyncio.sleep(0.05)
                owed, open_ = [i for i, s in ask.waiting(owed=True)], ask.waiting()
                tasks = sorted(bridge.resending)
                reach[0] = True
                bridge.reached("lap")  # the machine answers again
                for _ in range(700):
                    await asyncio.sleep(0.01)
                    if not bridge.resending:
                        break
            return stop_took, reap_took, owed, open_, tasks
        stop_took, reap_took, owed, open_, tasks = asyncio.run(go())
        self.assertLess(stop_took, 0.1)
        self.assertLess(reap_took, 0.1)
        self.assertEqual(ch.sent[-1].content, "done.", "the reply of this machine's turn was posted without the wait")
        self.assertEqual((owed, open_, tasks), (["7-1", "7-2", "7-3"], [], ["lap"]), "all three owed, none open, one task for the machine")
        self.assertEqual((ask.waiting(owed=True), bridge.resending), ([], {}), "handed over once the machine was back")
        self.assertEqual(sorted(set(c[2] for c in calls if c[1] == "answer")), ["7-1", "7-2", "7-3"])

    def test_deliver_to_says_whether_it_got_there(self):
        from bot import remote

        async def dcremote(host, *args, **kw):
            return (255, "") if host == "away" else (0, "")
        with mock.patch.object(remote, "dcremote", dcremote):
            self.assertEqual([asyncio.run(ask.deliver_to(h, "9-9", {})) for h in ("away", "lap", None)], [False, True, True])


if __name__ == "__main__":
    unittest.main()
