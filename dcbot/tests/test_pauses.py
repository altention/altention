"""C-184: a channel paused until a time (altention dc post --pause-until / --unpause), posts from other Claudes kept,
and the bot telling its Claude to carry on then (bot/pauses.py, through posts.Posts.handle)."""
import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))))
from altention import testing as _testing  # noqa: E402
_testing.guard_home()  # tests never touch the real home (altention/testing.py)
import asyncio
import calendar
import datetime
import importlib.machinery
import importlib.util
import os
import unittest

from tests import marks  # noqa: E402  (the quote's marks taken out: C-172a P1c)
from unittest import mock

from bot import pauses, posts, standby
from tests import tmuxguard  # noqa: F401  (no test reaches a real tmux server)
from tests.test_posts import Bot, Chan

NOW = 1_800_000_000


class Partial(object):
    def __init__(self, ch, mid):
        self.ch, self.mid = ch, mid

    async def edit(self, content=None, **kw):
        self.ch.edits.append((self.mid, content))


class Room(Chan):
    """A channel that also keeps the cards' footers and what was edited."""
    def __init__(self, cid, name):
        super().__init__(cid, name)
        self.footers, self.edits = [], []

    async def send(self, text=None, embed=None, **kw):
        if embed is not None:
            self.footers.append(getattr(embed.footer, "text", None))
        return await super().send(text, embed=embed, **kw)

    def get_partial_message(self, mid):
        return Partial(self, mid)


class Fails(Exception):
    pass


class Base(unittest.TestCase):
    def setUp(self):
        self.bot = Bot()
        self.a, self.b, self.notes = Room(1, "st-1"), Room(2, "st-0"), Room(3, "notes")
        self.bot.chans = {1: self.a, 2: self.b, 3: self.notes}
        self.bot.guild.text_channels = [self.a, self.b, self.notes]
        self.bot.channels_state = {"1": {"pane": {"id": "%1"}}, "2": {"tmux": "dc-x"}, "3": {"plain": True}}
        self.p = posts.Posts(self.bot)
        for patch in (mock.patch.object(pauses, "clock", lambda ts: "@%d" % int(ts)),
                      mock.patch.object(pauses.time, "time", lambda: self.now)):
            patch.start()
            self.addCleanup(patch.stop)
        self.now = NOW

    def handled(self, post):
        got = {"messages": []}

        async def go():
            posts.SENT.set(got)
            await self.p.handle(post)
            for _ in range(5):  # the tasks it started (later())
                await asyncio.sleep(0)
        asyncio.run(go())
        return posts.receipt_of(got)

    def chores(self):
        async def go():
            self.p.pauses.chores(self.now)
            for _ in range(10):
                await asyncio.sleep(0)
        asyncio.run(go())

    def pause(self, until=NOW + 3600, why="用量到線", sender="1"):
        return self.handled({"channel": "st-0", "pause": until, "why": why, "from_channel": sender, "wait": 30})

    def given_to(self, cid):
        return [t for c, t in self.bot.tmux.given if c == cid]


class Pausing(Base):
    def test_a_pause_is_posted_handed_over_kept_and_said(self):
        r = self.pause()
        self.assertEqual((r["ok"], r["text"]), (True, "已暫停 #st-0 到 @%d" % (NOW + 3600)))
        card = "⏸️ 暫停到 @%d（#st-1：用量到線）。" % (NOW + 3600)
        self.assertTrue(self.b.sent[-1].startswith(card), self.b.sent)
        self.assertEqual([t[:len(card)] for t in self.given_to(2)], [card], "its Claude is told as well")
        p = self.bot.channels_state["2"]["paused"]
        self.assertEqual((p["by"], p["by_ch"], p["why"], p["until"], p["at"], p["held"]),
                         ("#st-1", 1, "用量到線", NOW + 3600, NOW, []))
        self.assertEqual(self.bot.audits, ["#st-1（自稱） 暫停 #st-0 到 @%d：用量到線" % (NOW + 3600)])
        self.assertIn("paused", standby.MATTERS, "a standby that takes over resumes it too")

    def test_only_a_conversation_is_paused(self):
        r = self.handled({"channel": "notes", "pause": NOW + 60, "why": "w", "from_channel": "1", "wait": 30})
        self.assertEqual((r["ok"], r["text"]), (False, "#notes 不是有 Claude 的對話頻道，不能暫停"))
        self.assertNotIn("paused", self.bot.channels_state["3"])

    def test_pausing_again_replaces_it_and_keeps_what_was_held(self):
        self.pause()
        self.handled({"channel": "st-0", "text": "先看這個", "from_channel": "1"})
        r = self.pause(until=NOW + 7200, why="改到明天")
        self.assertEqual(r["text"], "已改成暫停 #st-0 到 @%d（原本到 @%d，#st-1 停的）" % (NOW + 7200, NOW + 3600))
        p = self.bot.channels_state["2"]["paused"]
        self.assertEqual((p["until"], len(p["held"])), (NOW + 7200, 1))
        self.assertIn("改成到 @%d" % (NOW + 7200), self.bot.audits[-1])

    def test_unpause_of_a_channel_not_paused(self):
        r = self.handled({"channel": "st-0", "unpause": True, "why": "w", "from_channel": "1", "wait": 30})
        self.assertEqual((r["ok"], r["text"]), (False, "#st-0 沒有在暫停"))
        self.assertEqual((self.b.sent, self.bot.audits), ([], []))


class WhilePaused(Base):
    def setUp(self):
        super().setUp()
        self.pause()
        self.bot.tmux.given.clear()

    def test_a_post_is_shown_with_a_note_kept_and_its_sender_told(self):
        r = self.handled({"channel": "st-0", "text": "review 好了", "from_channel": "1", "wait": 30})
        self.assertEqual(self.b.sent[-1], "📨 來自 #st-1\nreview 好了")
        self.assertEqual(self.b.footers[-1], "暫停中，@%d 恢復時、或使用者在這裡說話時交給 Claude" % (NOW + 3600))
        self.assertEqual(self.given_to(2), [], "not handed over")
        self.assertEqual(r["ok"], True)
        self.assertIn("#st-0 暫停到 @%d（用量到線），這則已經留著" % (NOW + 3600), r["text"])
        held, = self.bot.channels_state["2"]["paused"]["held"]
        self.assertEqual((held["sender"], held["from_ch"], held["text"], held["at"]), ("#st-1", 1, "review 好了", NOW))

    def test_a_sender_that_does_not_wait_is_told_in_its_channel(self):
        self.handled({"channel": "st-0", "text": "x", "from_channel": "1"})
        told, = self.given_to(1)
        self.assertTrue(told.startswith("（機器人通知）#st-0 暫停到"), told)

    def test_answer_and_talk_continue_are_refused_waiting_is_not(self):
        for post in ({"answer": "q-1", "pick": ["A"], "why": "w"}, {"talk_continue": True, "why": "w"}):
            r = self.handled(dict(post, channel="st-0", from_channel="1"))
            self.assertEqual((r["ok"], r["text"]), (False, "#st-0 暫停到 @%d，要先 --unpause --why" % (NOW + 3600)), post)
        with mock.patch("bot.agentanswer.waiting_said", lambda bridge, ch: "nothing"):
            self.assertEqual(self.handled({"channel": "st-0", "waiting": True, "from_channel": "1"})["text"], "nothing")

    def test_at_most_so_many_are_kept(self):
        for i in range(pauses.MOST_HELD):
            self.handled({"channel": "st-0", "text": "m%d" % i, "from_channel": "1"})
        r = self.handled({"channel": "st-0", "text": "one too many", "from_channel": "1", "wait": 30})
        self.assertEqual(r["ok"], False)
        self.assertIn("這則沒有留住", r["text"])
        self.assertEqual(len(self.bot.channels_state["2"]["paused"]["held"]), pauses.MOST_HELD)


class Resuming(Base):
    def setUp(self):
        super().setUp()
        self.pause()
        self.handled({"channel": "st-0", "text": "第一則", "from_channel": "1", "wait": 30})
        self.now += 60
        self.handled({"channel": "st-0", "text": "第二則", "from_channel": "1", "wait": 30})
        self.bot.tmux.given.clear()
        self.card = self.bot.channels_state["2"]["paused"]["msg"]

    def test_not_before_its_time(self):
        self.now = NOW + 3599
        self.chores()
        self.assertEqual(self.given_to(2), [])
        self.assertIn("paused", self.bot.channels_state["2"])

    def test_at_its_time_once_then_what_was_kept_in_order(self):
        self.now = NOW + 3600
        self.chores()
        resumed = "▶️ 恢復了：#st-1 在 @%d 叫你暫停到 @%d（用量到線）。照暫停前的進度繼續。" % (NOW, NOW + 3600)
        self.assertEqual(self.b.sent[-1], resumed)
        self.assertEqual(self.b.edits[-1][0], self.card)
        self.assertTrue(self.b.edits[-1][1].endswith("（已恢復）"))
        given = [marks.plain(t) for t in self.given_to(2)]
        self.assertEqual(given[0], resumed)
        self.assertIn("（暫停期間 @%d 由 #st-1 傳來）\n> 第一則" % NOW, given[1])
        self.assertIn("（暫停期間 @%d 由 #st-1 傳來）\n> 第二則" % (NOW + 60), given[2])
        self.assertEqual(len(given), 3)
        self.assertNotIn("paused", self.bot.channels_state["2"])
        self.assertIn("#st-0 恢復了", self.bot.audits[-1])
        self.chores()
        self.assertEqual(len(self.given_to(2)), 3, "said once")

    def test_a_bot_down_at_its_time_says_how_late(self):
        self.now = NOW + 3600 + 25 * 60
        self.chores()
        self.assertTrue(self.b.sent[-1].endswith("（晚了 25 分鐘：那時機器人沒在跑）"), self.b.sent[-1])

    def test_paused_again_while_what_was_kept_is_handed_over(self):
        """#code-review on 8738b973: its own Claude, told to carry on, pauses itself again while the kept posts are
        still going in; the new pause stays, and is resumed at its time."""
        gate, real = asyncio.Event(), self.bot.tmux.submit

        async def submit(origin, text, **kw):
            if "第一則" in text:
                await gate.wait()
            await real(origin, text)
        self.bot.tmux.submit = submit
        self.now = NOW + 3600
        got = {"messages": []}

        async def go():
            posts.SENT.set(got)
            self.p.pauses.chores(self.now)
            for _ in range(10):
                await asyncio.sleep(0)
            await self.p.handle({"channel": "st-0", "pause": NOW + 7200, "why": "CI 還沒好", "from_channel": "2", "wait": 30})
            await self.p.handle({"channel": "st-0", "text": "新留的", "from_channel": "1", "wait": 30})
            gate.set()
            for _ in range(20):
                await asyncio.sleep(0)
        asyncio.run(go())
        p = self.bot.channels_state["2"].get("paused")
        self.assertEqual((p or {}).get("until"), NOW + 7200, "the new pause is kept")
        self.assertFalse(p.get("resumed"))
        went = [t for t in self.given_to(2) if "暫停期間" in t]
        self.assertEqual(len(went), 1, "the one going in when it was paused again")
        self.assertIn("第一則", went[0])
        self.assertEqual([h["text"] for h in p["held"]], ["第二則", "新留的"], "the rest is the new pause's, in order")
        self.now = NOW + 7200
        self.chores()
        self.assertTrue(self.b.sent[-1].startswith("▶️ 恢復了：#st-0"), self.b.sent[-1])
        went = [t for t in self.given_to(2) if "暫停期間" in t]
        self.assertEqual([("第二則" in t, "新留的" in t) for t in went[1:]], [(True, False), (False, True)], "each once, in order")
        self.assertNotIn("paused", self.bot.channels_state["2"])

    def test_paused_again_and_the_one_going_in_fails(self):
        """#code-review on 2d74f22f: the post going in when it was paused again fails; it is the new pause's first."""
        gate, real = asyncio.Event(), self.bot.tmux.submit

        async def submit(origin, text, **kw):
            if "第一則" in text and not gate.is_set():
                await gate.wait()
                raise Fails("a dialog")
            await real(origin, text)
        self.bot.tmux.submit = submit
        self.now = NOW + 3600

        async def go():
            posts.SENT.set({"messages": []})
            self.p.pauses.chores(self.now)
            for _ in range(10):
                await asyncio.sleep(0)
            await self.p.handle({"channel": "st-0", "pause": NOW + 7200, "why": "CI 還沒好", "from_channel": "2", "wait": 30})
            gate.set()
            for _ in range(20):
                await asyncio.sleep(0)
        asyncio.run(go())
        p = self.bot.channels_state["2"]["paused"]
        self.assertEqual([h["text"] for h in p["held"]], ["第一則", "第二則"], "not lost, still first")
        self.now = NOW + 7200
        self.chores()
        went = [t for t in self.given_to(2) if "暫停期間" in t]
        self.assertEqual([("第一則" in t, "第二則" in t) for t in went], [(True, False), (False, True)])
        self.assertNotIn("paused", self.bot.channels_state["2"])

    def test_a_card_discord_does_not_take_its_claude_is_told_all_the_same(self):
        """#code-review-2 on 8738b973: the ▶️ card fails; its Claude still gets the words, #audit says so once, the card
        goes the next minute."""
        real, fails = self.b.send, [2]

        async def send(text=None, **kw):
            if (text or "").startswith("▶️") and fails[0]:
                fails[0] -= 1
                raise Fails("503")
            return await real(text, **kw)
        self.b.send = send
        self.now = NOW + 3600
        self.chores()
        given = self.given_to(2)
        self.assertTrue(given[0].startswith("▶️ 恢復了"), given)
        self.assertEqual(len(given), 3, "and what was kept")
        self.assertEqual(sum("卡片沒貼出來" in a for a in self.bot.audits), 1)
        self.assertIn("paused", self.bot.channels_state["2"], "the card is still owed")
        self.now += 60
        self.chores()
        self.assertEqual(sum("卡片沒貼出來" in a for a in self.bot.audits), 1, "said once")
        self.now += 60
        self.chores()
        self.assertTrue(self.b.sent[-1].startswith("▶️ 恢復了"))
        self.assertNotIn("paused", self.bot.channels_state["2"])
        self.assertEqual(len(self.given_to(2)), 3, "nothing handed over twice")

    def test_claude_not_taking_the_words_is_told_again_marked(self):
        """At least once (manager on 8738b973): its Claude does not take the ▶️ words; #audit says so once, the next
        minute they go again, marked 重送, and only then what was kept."""
        real, fails = self.bot.tmux.submit, [1]

        async def submit(origin, text, **kw):
            if text.startswith("▶️") and fails[0]:
                fails[0] -= 1
                raise Fails("a dialog")
            await real(origin, text)
        self.bot.tmux.submit = submit
        self.now = NOW + 3600
        self.chores()
        self.assertEqual(self.given_to(2), [], "the kept ones wait for it")
        self.assertIn("沒收到「恢復了」", self.bot.audits[-1])
        self.now += 60
        self.chores()
        given = self.given_to(2)
        self.assertTrue(given[0].startswith("▶️ 恢復了") and given[0].endswith("（重送）"), given[0])
        self.assertEqual(len(given), 3)
        self.assertEqual(sum(t.startswith("▶️") for t in self.b.sent), 1, "one card on Discord")

    def test_a_restart_before_its_claude_took_the_words(self):
        """The bot stopped after writing `resumed` and posting the card, before its Claude took the words."""
        p = self.bot.channels_state["2"]["paused"]
        p.update(resumed=NOW + 3600, shown=True, said="▶️ 恢復了：…")
        self.p = posts.Posts(self.bot)  # (a new bot: nothing in memory)
        self.now = NOW + 3700
        self.chores()
        given = self.given_to(2)
        self.assertEqual(given[0], "▶️ 恢復了：…（重送）")
        self.assertEqual(len(given), 3)
        self.assertEqual(self.b.sent[-1], "📨 來自 #st-1\n第二則", "no second card")

    def test_kept_posts_for_a_channel_with_no_conversation_any_more_are_said(self):
        self.bot.channels_state["2"].pop("tmux")
        self.now = NOW + 3600
        self.chores()
        self.assertNotIn("paused", self.bot.channels_state["2"])
        self.assertEqual(self.bot.audits[-1], "#st-0 恢復了，但那裡已經沒有 Claude 對話，留著的 2 則沒有交（卡片都還在頻道裡）")

    def test_unpause_now_says_who_and_why(self):
        r = self.handled({"channel": "st-0", "unpause": True, "why": "急件", "from_channel": "1", "wait": 30})
        self.assertEqual((r["ok"], r["text"]), (True, "已恢復 #st-0，留著的 2 則接著交給它"))
        self.assertTrue(self.b.sent[-1].startswith("▶️ 提早恢復（#st-1：急件）"), self.b.sent[-1])
        self.assertEqual(len(self.given_to(2)), 3)
        self.assertEqual(self.bot.audits[-1], "#st-1（自稱） 提早恢復 #st-0：急件")

    def test_one_not_handed_over_stays_kept_and_goes_the_next_minute(self):
        calls = []
        real = self.bot.tmux.submit

        async def submit(origin, text, **kw):
            calls.append(text)
            if "第一則" in text and len(calls) == 2:
                raise Fails("a dialog")
            await real(origin, text)
        self.bot.tmux.submit = submit
        self.now = NOW + 3600
        self.chores()
        p = self.bot.channels_state["2"]["paused"]
        self.assertEqual((bool(p.get("resumed")), [h["text"] for h in p["held"]]), (True, ["第一則", "第二則"]))
        self.assertIn("交不進去", self.bot.audits[-1])
        said = len(self.bot.audits)
        self.assertIsNone(self.p.pauses.of(2), "resumed: a new post is handed over, not kept")
        self.now += 60
        self.chores()
        self.assertNotIn("paused", self.bot.channels_state["2"])
        self.assertEqual(sum("▶️" in t for t in calls), 1, "the ▶️ card once")
        self.assertEqual(len(self.bot.audits), said, "nothing more said")

    def test_a_failure_that_lasts_is_said_once(self):
        async def submit(origin, text, **kw):
            if "暫停期間" in text:
                raise Fails("a dialog")
        self.bot.tmux.submit = submit
        for i in range(4):
            self.now = NOW + 3600 + 60 * i
            self.chores()
        self.assertEqual(sum("交不進去" in a for a in self.bot.audits), 1)


class Woken(Resuming):
    """C-199 (owner's choice, manager 11-17): the owner speaking in the paused channel takes what was held along,
    right after that message; the pause goes on; the channel's own Claude may drop what is held."""
    def wake(self):
        async def go():
            await self.p.pauses.wake(self.b)
            for _ in range(5):
                await asyncio.sleep(0)
        asyncio.run(go())

    def held(self):
        return [h["text"] for h in self.bot.channels_state["2"]["paused"]["held"]]

    def test_handed_over_in_order_and_not_again_at_its_time(self):
        self.wake()
        given = [marks.plain(t) for t in self.given_to(2)]
        self.assertEqual(len(given), 2)
        self.assertIn("（暫停期間 @%d 由 #st-1 傳來）\n> 第一則" % NOW, given[0])
        self.assertIn("> 第二則", given[1])
        self.assertEqual(self.held(), [])
        self.assertIsNotNone(self.p.pauses.of(2), "the pause goes on")
        self.assertEqual(self.bot.audits[-1], "#st-0 暫停中，使用者說話時把留著的 2 則接在後面交給了 Claude（暫停照舊到 @%d）" % (NOW + 3600))
        self.handled({"channel": "st-0", "text": "第三則", "from_channel": "1", "wait": 30})
        self.assertEqual(self.held(), ["第三則"], "what comes after is held as ever")
        self.bot.tmux.given.clear()
        self.now = NOW + 3600
        self.chores()
        given = [marks.plain(t) for t in self.given_to(2)]
        self.assertTrue(given[0].startswith("▶️ 恢復了"))
        self.assertEqual([("第一則" in t, "第二則" in t, "第三則" in t) for t in given[1:]], [(False, False, True)],
                         "at its time: the ▶️ words and only what came after (manager 11-17)")

    def test_one_not_handed_over_stays_and_goes_later(self):
        real = self.bot.tmux.submit

        async def submit(origin, text, **kw):
            if "第二則" in text:
                raise Fails("a dialog")
            await real(origin, text)
        self.bot.tmux.submit = submit
        self.wake()
        self.assertEqual(self.held(), ["第二則"])
        self.assertIn("交不進去（a dialog），還剩 1 則", self.bot.audits[-2])
        self.assertIn("交給了 Claude（暫停照舊到 @%d；還有 1 則沒交進去，之後再交）" % (NOW + 3600), self.bot.audits[-1])
        self.bot.tmux.submit = real
        self.wake()
        self.assertEqual(self.held(), [], "the owner's next message takes it")
        self.assertEqual(sum("第二則" in t for t in self.given_to(2)), 1)

    def test_a_post_that_comes_while_it_hands_over_stays_held(self):
        real = self.bot.tmux.submit

        async def submit(origin, text, **kw):
            if "第一則" in text:
                self.p.pauses.of(2)["held"].append({"sender": "#st-1", "from_ch": 1, "text": "途中來的", "files": [],
                                                    "at": NOW, "now": False})
            await real(origin, text)
        self.bot.tmux.submit = submit
        self.wake()
        self.assertEqual(self.held(), ["途中來的"], "only what was held when the owner spoke")

    def test_not_while_a_resume_hands_over_and_a_resume_waits_for_it(self):
        self.p.pauses.resuming.add("2")
        self.wake()
        self.assertEqual((self.given_to(2), len(self.held())), ([], 2), "the resume hands it all")
        self.p.pauses.resuming.discard("2")
        self.p.pauses.waking.add("2")
        self.now = NOW + 3600
        started = []
        with mock.patch.object(self.p, "later", lambda coro: (started.append(coro), coro.close())):
            self.chores()
        self.assertEqual(started, [], "chores start no resume of a channel whose wake is going")
        with mock.patch.object(pauses, "WAKE_WAIT", 0):
            async def go():
                waiting = asyncio.ensure_future(self.p.pauses.resume(self.b))
                for _ in range(5):
                    await asyncio.sleep(0)
                self.assertEqual(self.given_to(2), [], "a resume waits for the wake")
                self.p.pauses.waking.discard("2")
                await waiting
            asyncio.run(go())
        self.assertTrue(self.given_to(2)[0].startswith("▶️"), "then goes on")

    def test_not_paused_or_nothing_held(self):
        self.p.pauses.of(2)["held"].clear()
        self.wake()
        self.assertEqual(self.given_to(2), [])
        self.bot.channels_state["2"].pop("paused")
        self.wake()
        self.assertEqual(self.given_to(2), [])

    def drop(self, sender, verified=True):
        return self.handled({"channel": "st-0", "drop_held": True, "why": "自己讀過了", "from_channel": sender,
                             "verified": verified, "wait": 30})

    def test_dropped_by_its_own_claude_only(self):
        r = self.drop("1")
        self.assertEqual((r["ok"], r["text"]), (False, "只有 #st-0 自己的 Claude（查證過的）能清它留著的訊息"))
        self.assertEqual(self.bot.audits[-1], "#st-1（查證過） 想清 #st-0 留著的訊息，被拒：不是那個頻道自己的 Claude（查證過的）"
                                              "（理由：自己讀過了）", "another's held posts: said (manager 11-17)")
        r = self.drop("2", verified=False)
        self.assertFalse(r["ok"], "said, not looked up")
        self.assertIn("#st-0（自稱） 想清 #st-0 留著的訊息，被拒", self.bot.audits[-1])
        self.assertEqual(len(self.held()), 2)
        r = self.drop("2")
        self.assertEqual((r["ok"], r["text"]), (True, "清掉了 #st-0 留著的 2 則；恢復時不會再交"))
        self.assertEqual(self.held(), [])
        self.assertEqual(self.bot.audits[-1], "#st-0（查證過） 清掉了 #st-0 留著的 2 則（暫停中，不交了）：自己讀過了")
        self.assertEqual(self.drop("2")["text"], "#st-0 沒有留著的訊息")
        self.bot.channels_state["2"].pop("paused")
        self.assertEqual(self.drop("2")["text"], "#st-0 沒有在暫停")


class OwnerSpeaks(unittest.TestCase):
    """main.Bot.deliver() (C-199, manager 11-17): the paused channel's held posts go in only once TmuxBridge.submit says
    the owner's message went in as a plain message (its `typed`, tested with the real submit in test_safety); /exit
    waiting on its button, /logout, a message not sent: submit never says so, and nothing is handed over; print mode:
    the resume hands them over."""
    def deliver(self, went, paused=True, mode="tmux"):
        from bot import main
        bot = mock.Mock()
        bot.chan.return_value = {}
        bot.needs_setup.return_value = False
        bot.mode.return_value = mode
        bot.tmux.asks.typed = mock.AsyncMock(return_value=False)
        bot.print_turn = mock.AsyncMock()

        async def submit(message, text, now=False, typed=None):
            if went:
                typed()
        bot.tmux.submit = submit
        bot.posts.pauses.of.return_value = {"held": [{"text": "x"}]} if paused else None
        bot.woken = lambda ch: main.Bot.woken(bot, ch)
        asyncio.run(main.Bot.deliver(bot, mock.Mock(channel=mock.Mock(id=2)), "醒醒"))
        return bot.posts.later.call_count

    def test_only_after_it_went_in(self):
        self.assertEqual(self.deliver(True), 1)
        self.assertEqual(self.deliver(False), 0, "not typed in, refused, waiting on a button: held stays")
        self.assertEqual(self.deliver(True, paused=False), 0, "not paused")
        self.assertEqual(self.deliver(True, mode="print"), 0, "print mode: the resume hands it over")


ENGINE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def load(name, *where):
    loader = importlib.machinery.SourceFileLoader(name, os.path.join(ENGINE, *where))
    mod = importlib.util.module_from_spec(importlib.util.spec_from_loader(name, loader))
    loader.exec_module(mod)
    return mod


class Fixed(object):
    """A zone eight hours east of UTC, no summer time: the pausetime questions, answered plainly."""
    def local(self, ts):
        return datetime.datetime.utcfromtimestamp(int(ts) + 8 * 3600)

    def resolve(self, naive):
        return calendar.timegm(naive.timetuple()) - 8 * 3600


class Times(unittest.TestCase):
    def setUp(self):
        from altention.dc import pausetime
        self.t, self.tz = pausetime, Fixed()
        self.now = calendar.timegm((2026, 11, 11, 15, 10, 0)) - 8 * 3600  # 2026-11-11 15:10 there

    def parse(self, text):
        return self.t.parse(text, self.now, self.tz)

    def at(self, *wall):
        return calendar.timegm(wall + (0,) * (6 - len(wall))) - 8 * 3600

    def test_the_ways_to_write_it(self):
        self.assertEqual(self.parse("00:00"), (self.at(2026, 11, 12, 0, 0), None), "the coming midnight")
        self.assertEqual(self.parse("16:30"), (self.at(2026, 11, 11, 16, 30), None), "later today")
        self.assertEqual(self.parse("09:00"), (self.at(2026, 11, 12, 9, 0), None), "past today: tomorrow")
        self.assertEqual(self.parse("2026-11-13 08:00"), (self.at(2026, 11, 13, 8, 0), None))
        self.assertEqual(self.parse("30m"), (self.now + 1800, None))
        self.assertEqual(self.parse("2h"), (self.now + 7200, None))
        self.assertEqual(self.parse(" 1d "), (self.now + 86400, None))

    def test_refused(self):
        for text, why in (("2026-11-11 15:00", "已經過了"), ("0m", "已經過了"), ("8d", "最多暫停 7 天"),
                          ("2026-11-30 08:00", "最多暫停 7 天"), ("soon", "看不懂時間"), ("25:00", "沒有這個時刻"),
                          ("2026-02-30 08:00", "沒有這個時刻")):
            until, said = self.parse(text)
            self.assertIsNone(until, text)
            self.assertIn(why, said, text)

    def test_shown(self):
        self.assertEqual(self.t.shown(self.at(2026, 11, 11, 23, 5), self.now, self.tz), "23:05")
        self.assertEqual(self.t.shown(self.at(2026, 11, 12, 0, 0), self.now, self.tz), "11-12 00:00")


class CommandLine(unittest.TestCase):
    def setUp(self):
        self.dcpost = load("dcpost_c184", "altention", "dc", "post.py")

    def build(self, *argv):
        return self.dcpost.build(list(argv), "", {"DCBOT_CHANNEL": "55"}, "host-a", "/w/proj", claude={"DCBOT_CHANNEL": "55"})

    def test_pause_and_unpause(self):
        with mock.patch("altention.dc.pausetime.parse", lambda text: (NOW, None) if text == "00:00" else (None, "x")):
            p = self.build("st-0", "--pause-until", "00:00", "--why", "用量到線")
        self.assertEqual((p["pause"], p["why"], p["wait"], p["from_channel"], p["text"]), (NOW, "用量到線", 30, "55", ""))
        p = self.build("st-0", "--unpause", "--why", "急件")
        self.assertEqual((p.get("unpause"), p["why"], p["wait"]), (True, "急件", 30))
        p = self.build("st-0", "--drop-held", "--why", "讀過了")
        self.assertEqual((p.get("drop_held"), p["why"], p["wait"], p["from_channel"]), (True, "讀過了", 30, "55"))

    def refused(self, *argv):
        with mock.patch("altention.dc.pausetime.parse", lambda text: (None, "已經過了") if text == "08:00" else (NOW, None)):
            with self.assertRaises(SystemExit) as e:
                self.build(*argv)
        return str(e.exception.code)

    def test_refused(self):
        self.assertIn("要帶 --why", self.refused("st-0", "--pause-until", "00:00"))
        self.assertIn("要帶 --why", self.refused("st-0", "--unpause"))
        self.assertIn("單獨用", self.refused("st-0", "--pause-until", "00:00", "--why", "w", "還有一句"))
        self.assertIn("單獨用", self.refused("st-0", "--unpause", "--why", "w", "--talk-continue"))
        self.assertIn("不能一起用", self.refused("st-0", "--unpause", "--pause-until", "00:00", "--why", "w"))
        self.assertIn("--pause-until 已經過了", self.refused("st-0", "--pause-until", "08:00", "--why", "w"))
        self.assertIn("要給時間", self.refused("st-0", "--pause-until"))
        self.assertIn("要帶 --why", self.refused("st-0", "--drop-held"))
        self.assertIn("單獨用", self.refused("st-0", "--drop-held", "--why", "w", "還有一句"))
        self.assertIn("不能一起用", self.refused("st-0", "--drop-held", "--unpause", "--why", "w"))


class Listed(unittest.TestCase):
    """altention dc read --paused (dcctl paused) and the 暫停中的頻道 lines of the waiting board."""

    def state(self):
        return {"2": {"paused": {"by": "#st-1", "why": "用量到線", "until": NOW + 3600, "at": NOW,
                                 "held": [{"text": "a"}, {"text": "b"}]}},
                "4": {"paused": {"by": "#mgr", "why": "w", "until": NOW - 60, "at": NOW - 600, "held": []}},
                "5": {"paused": {"by": "#mgr", "why": "w", "until": NOW - 60, "resumed": NOW, "held": [{"text": "c"}]}},
                "6": {"tmux": "dc-x"}}

    def test_dcctl(self):
        tool = load("dcctl_c184", "dcbot", "tools", "dcctl")
        with mock.patch("altention.dc.pausetime.shown", lambda ts, now=None: "@%d" % ts):
            said = tool.paused_said(self.state(), {"2": "st-0", "4": "st-4"}, NOW)
            none = tool.paused_said({"6": {}}, {}, NOW)
        self.assertEqual(said.splitlines(), [
            "暫停中的頻道：",
            "- #st-0（2）· 到 @%d 恢復 · #st-1：用量到線 · 留著 2 則" % (NOW + 3600),
            "- #st-4（4）· 應該在 @%d 恢復，機器人上線後會補叫 · #mgr：w" % (NOW - 60),
            "- #?（5）· 已恢復，留著的 1 則還在交給它 · #mgr：w"])
        self.assertEqual(none, "目前沒有暫停中的頻道")
        read = load("dcread_c184", "altention", "dc", "read.py")
        self.assertEqual(read.dcctl_args(["--paused"]), ["paused"])

    def test_board(self):
        boards = load("status_boards_c184", "status", "boards.py")
        rows = [r[2] for r in sorted(boards.paused_rows(self.state(), NOW))]
        self.assertEqual(rows, ["- <#4> · 應該在 <t:%d:t> 恢復，機器人上線後會補叫" % (NOW - 60),
                                "- <#2> · 到 <t:%d:t> 恢復 · #st-1：用量到線 · 留著 2 則" % (NOW + 3600)])
        self.assertEqual(boards.paused_rows({"6": {}}, NOW), [])


if __name__ == "__main__":
    unittest.main()
