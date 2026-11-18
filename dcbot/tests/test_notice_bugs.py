"""Bugs found taking stock of the bot's notices (instance docs/refactor/notice-design.md §7; manager 11-16):
1 a plan approved or sent back closes the ⏸️ line in the channel that asked; 2 posts held by the talk limit one after
another wait behind the first card, none lost; 3 an urgent "!!" message is never taken as the answer to the one open
question; 6 the archive refusal names the terminal's real button. (5, the 填入 card marked filled: test_secretbox.)"""
import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))))
from altention import testing as _testing  # noqa: E402
_testing.guard_home()  # tests never touch the real home (altention/testing.py)
import asyncio
import os
import shutil
import tempfile
import types
import unittest

from tests import marks  # noqa: E402  (the quote's marks taken out: C-172a P1c)
from unittest import mock

from bot import agentanswer, ask, config, posts, tmux_bridge
from tests import tmuxguard  # noqa: F401  (no test reaches a real tmux server)
from tests.test_ask import Click, FakeChannel
from tests.test_posts import Bot, Chan
from tests.test_turn_end import Ch, Host


class Card(object):
    def __init__(self, ch, i, text):
        self.ch, self.id, self.content = ch, i, text

    async def edit(self, content=None, view=None):
        self.content = content
        self.ch.edits.append((self.id, content))


class Carded(Chan):
    """Sends return a message that can be edited, and the buttons sent are kept."""
    def __init__(self, cid, name):
        Chan.__init__(self, cid, name)
        self.edits, self.views, self.cards = [], [], {}

    async def send(self, text=None, view=None, **kw):
        await Chan.send(self, text, **kw)
        self.views.append([i.custom_id for i in view.children] if view is not None else None)
        self.cards[len(self.sent)] = Card(self, len(self.sent), text)
        return self.cards[len(self.sent)]

    def get_partial_message(self, mid):
        return self.cards.get(mid) or Card(self, mid, None)


class Press(object):
    """A click on the hold card's buttons (Posts.on_interaction)."""
    def __init__(self, ch, what):
        self.type = posts.discord.InteractionType.component
        self.channel, self.data = ch, {"custom_id": "post:%d:%s" % (ch.id, what)}
        self.user = types.SimpleNamespace(id=config.OWNER_ID)
        self.edited = None
        press = self

        class Response(object):
            async def edit_message(self, content=None, view=None):
                press.edited = content
        self.response = Response()


class PlanLine(unittest.TestCase):
    """Bug 1: Posts.asking() said ⏸️ in the sending channel; approving or sending back the plan never changed it."""
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        for p in (mock.patch.object(ask, "ASKS", os.path.join(tmp.name, "asks")),
                  mock.patch.object(ask, "ANSWERS", os.path.join(tmp.name, "answers")),
                  mock.patch.object(config, "OWNER_ID", 111)):
            p.start()
            self.addCleanup(p.stop)
        self.done = []

        async def done(ch, ask_id, how):
            self.done.append((ask_id, how))

        async def audit(text):
            pass
        self.asker, self.ch = ask.Asker(audit), FakeChannel()
        self.asker.on_done = done

    def test_the_owners_buttons(self):
        async def go():
            await self.asker.post_plan(self.ch, {"ask_id": "9-1", "plan": "p"})
            await self.asker.on_interaction(Click(self.ch, "ask:9-1:0:y"))
            await self.asker.post_plan(self.ch, {"ask_id": "9-2", "plan": "p"})
            change = Click(self.ch, "ask:9-2:0:o")
            await self.asker.on_interaction(change)
            await self.asker.record(change, "9-2", 0, "smaller")
        asyncio.run(go())
        self.assertEqual(self.done, [("9-1", "計畫已核准"), ("9-2", "計畫已退回修改")])

    def test_another_channels_claude(self):
        ch = Ch()
        bridge = tmux_bridge.TmuxBridge(Host(ch))
        got = []

        async def done(c, ask_id, how):
            got.append((ask_id, how))
        bridge.asks.on_done = done
        sender = types.SimpleNamespace(id=7, name="lead")
        for ask_id in ("5-1", "5-2"):
            mid = asyncio.run(ch.send("card")).id
            ask.save(ask_id, {"channel": str(ch.id), "kind": "plan", "questions": [{"question": "plan"}], "answers": {},
                              "messages": {"0": mid}})
        asyncio.run(agentanswer.answer(bridge, ch, sender, {"answer": "q-5-1", "approve": True, "why": "w"}))
        asyncio.run(agentanswer.answer(bridge, ch, sender, {"answer": "q-5-2", "revise": "less", "why": "w"}))
        self.assertEqual(got, [("5-1", "計畫已由 #lead 的 Claude 核准"), ("5-2", "計畫已由 #lead 的 Claude 退回修改")])

    def test_the_line_says_so(self):
        bot = Bot()
        sender, target = Carded(1, "lead"), Carded(2, "st-0")
        bot.chans = {1: sender, 2: target}
        bot.channels_state = {"2": {"asking": {"9-1": [1, 5]}}}
        p = posts.Posts(bot)
        for how, said in (("計畫已核准", "✅ <#2> 的計畫已核准，它會接著做。"),
                          ("計畫已由 #lead 的 Claude 退回修改", "✅ <#2> 的計畫已由 #lead 的 Claude 退回修改，它會接著做。"),
                          ("已回答", "✅ <#2> 的提問已回答，它會接著做。"), ("已取消", "⌛ <#2> 的提問已取消。")):
            bot.channels_state["2"]["asking"] = {"9-1": [1, 5]}
            asyncio.run(p.answered(target, "9-1", how))
            self.assertEqual(sender.edits[-1], (5, said))
            self.assertEqual(bot.channels_state["2"]["asking"], {}, "no longer waited on")


class HeldBehind(unittest.TestCase):
    """Bug 2: a second post over the talk limit took the first one's place (that one lost, the first card's buttons
    acting on the second, a second ring)."""
    def setUp(self):
        self.bot = Bot()
        self.a, self.b, self.c = Carded(1, "st-1"), Carded(2, "st-0"), Carded(3, "st-2")
        self.bot.chans = {1: self.a, 2: self.b, 3: self.c}
        self.bot.guild.text_channels = [self.a, self.b, self.c]
        self.bot.channels_state = {"1": {"pane": {"id": "%1"}, "talk_limit": 2}, "2": {"tmux": "dc-x", "talk_limit": 2},
                                   "3": {"pane": {"id": "%3"}, "talk_limit": 2}}  # (a pair's limit: the larger one)
        self.p = posts.Posts(self.bot)
        p = mock.patch.object(config, "OWNER_ID", 42)
        p.start()
        self.addCleanup(p.stop)

    def handled(self, text, frm="1"):
        async def go():
            await self.p.handle(dict(channel="st-0", text=text, from_channel=frm, verified=True))
            for _ in range(5):
                await asyncio.sleep(0)
        asyncio.run(go())

    def given(self):
        return [marks.plain(t).split("\n")[-1].lstrip("> ") for _, t in self.bot.tmux.given]

    def three_held(self):
        for i in range(5):
            self.handled("m%d" % i)
        self.assertEqual(self.given(), ["m0", "m1"])
        self.assertEqual([v for v in self.b.views if v], [["post:2:go", "post:2:free", "post:2:stop"]], "one card")
        self.assertEqual(len(self.bot.tmux.calls), 1, "one ring")
        self.assertEqual(self.p.held[2]["text"], "m2", "the first held stays the first")
        card = self.p.hold_cards[2][0]
        self.assertIn("之後又來了 2 則，也先留著", card.content)

    def test_go_hands_them_all_in_order(self):
        self.three_held()
        press = Press(self.b, "go")
        self.assertTrue(asyncio.run(self.p.on_interaction(press)))
        self.assertEqual(self.given(), ["m0", "m1", "m2", "m3", "m4"])
        self.assertEqual((self.p.held, self.p.held_more, self.p.hold_cards), ({}, {}, {}))
        self.assertEqual(len(self.bot.tmux.done), 1, "the one ring marked done")

    def test_stop_hands_none(self):
        self.three_held()
        press = Press(self.b, "stop")
        asyncio.run(self.p.on_interaction(press))
        self.assertEqual(press.edited, "⏹️ 停下來了，那 3 則沒有交給 Claude。")
        self.assertEqual(self.given(), ["m0", "m1"])
        self.assertEqual(self.p.held_more, {})
        self.handled("next")  # (the owner decided: a fresh stretch)
        self.assertEqual(self.given(), ["m0", "m1", "next"])

    def test_free_lets_every_sender_go(self):
        for i in range(3):
            self.handled("m%d" % i)
        self.p.held_more[2] = [dict(channel="st-0", text="from st-2", from_channel="3", verified=True)]
        asyncio.run(self.p.on_interaction(Press(self.b, "free")))
        self.assertEqual(self.bot.channels_state["2"]["talk_free"], [1, 3])
        self.assertEqual(self.given()[-2:], ["m2", "from st-2"])

    def test_talk_continue_hands_them_all(self):
        self.three_held()
        ok, said = asyncio.run(self.p.talk_continue(self.b, self.a, {"why": "w", "verified": True}))
        self.assertTrue(ok, said)
        self.assertEqual(self.given(), ["m0", "m1", "m2", "m3", "m4"])
        self.assertEqual(self.p.held_more, {})

    def test_talk_continue_lets_only_its_own_pair_go(self):
        """manager 11-16 (b), #code-review on f298058b: a Claude decides for its own pair only; another pair's posts on
        the same card stay for the owner, or for that pair's own Claude."""
        self.three_held()
        self.p.held_more[2].insert(1, dict(channel="st-0", text="from st-2", from_channel="3", verified=True))
        ok, said = asyncio.run(self.p.talk_continue(self.b, self.a, {"why": "w", "verified": True}))
        self.assertTrue(ok, said)
        self.assertEqual(self.given(), ["m0", "m1", "m2", "m3", "m4"], "st-1's, in order; st-2's left")
        self.assertEqual(self.p.held[2]["text"], "from st-2")
        self.assertEqual(self.p.held_more, {})
        self.assertEqual(self.bot.tmux.done, [], "the ring stands: the owner still has one to decide")
        self.assertIn("#st-1 那組的已經由它的 Claude 繼續轉交；還留著 1 則別組的，等你決定", self.p.hold_cards[2][0].content)
        self.assertEqual(self.bot.channels_state["2"]["talk_continued"].keys(), {"1"}, "counted for st-1's pair only")
        ok, said = asyncio.run(self.p.talk_continue(self.b, self.c, {"why": "w", "verified": True}))
        self.assertTrue(ok, said)
        self.assertEqual(self.given()[-1], "from st-2")
        self.assertEqual((self.p.held, self.p.hold_cards, len(self.bot.tmux.done)), ({}, {}, 1))
        self.assertEqual(sorted(self.bot.channels_state["2"]["talk_continued"]), ["1", "3"])

    def test_talk_continue_with_another_pairs_post_first(self):
        self.three_held()
        first = self.p.held[2]
        self.p.held[2] = dict(channel="st-0", text="from st-2", from_channel="3", verified=True)
        self.p.held_more[2].insert(0, first)
        ok, said = asyncio.run(self.p.talk_continue(self.b, self.a, {"why": "w", "verified": True}))
        self.assertTrue(ok, said)
        self.assertEqual(self.given(), ["m0", "m1", "m2", "m3", "m4"])
        self.assertEqual((self.p.held[2]["text"], self.p.held_more), ("from st-2", {}))
        asyncio.run(self.p.on_interaction(Press(self.b, "stop")))
        self.assertEqual(self.given()[-1], "m4", "the owner stopped st-2's")

    def test_held_survives_a_restart(self):
        """C-193: what is held is in the channel's state (talk_held: the posts without their files' bytes, the card,
        its words, its mention), so a bot started again (or the standby taking over: standby.MATTERS) lets it go."""
        import base64
        from bot import standby
        self.three_held()
        self.p.held_more[2][0]["file_name"], self.p.held_more[2][0]["file_b64"] = "a.txt", base64.b64encode(b"x").decode()
        self.p.keep_held(2)
        th = self.bot.channels_state["2"]["talk_held"]
        self.assertEqual([q["text"] for q in th["posts"]], ["m2", "m3", "m4"])
        self.assertNotIn("file_b64", th["posts"][1], "the file went out with its card: its bytes are not kept")
        self.assertEqual(th["posts"][1]["held_files"], ["a.txt"])
        self.assertEqual((th["card"], th["call"]), (self.p.hold_cards[2][0].id, self.p.holds_called[2]))
        self.assertIn("talk_held", standby.MATTERS)
        self.p = posts.Posts(self.bot)  # (a restart: nothing in memory)
        self.p.restore_held()
        self.assertEqual([self.p.held[2]["text"]] + [q["text"] for q in self.p.held_more[2]], ["m2", "m3", "m4"])
        self.assertTrue(asyncio.run(self.p.on_interaction(Press(self.b, "go"))))
        self.assertEqual([t for t in self.given() if not t.startswith("（附件")], ["m0", "m1", "m2", "m4"])
        self.assertIn("m3", self.bot.tmux.given[3][1])
        self.assertIn("a.txt", self.bot.tmux.given[3][1], "its file named to Claude as before")
        self.assertNotIn("talk_held", self.bot.channels_state["2"], "let go: nothing kept")
        self.assertEqual(len(self.bot.tmux.done), 1, "the mention kept across the restart, marked done")

    def test_held_state_follows_every_change(self):
        self.three_held()
        self.p.held_more[2].insert(0, dict(channel="st-0", text="from st-2", from_channel="3", verified=True))
        self.p.keep_held(2)
        asyncio.run(self.p.talk_continue(self.b, self.a, {"why": "w", "verified": True}))
        self.assertEqual([q["text"] for q in self.bot.channels_state["2"]["talk_held"]["posts"]], ["from st-2"],
                         "another pair's left, kept")
        asyncio.run(self.p.on_interaction(Press(self.b, "stop")))
        self.assertNotIn("talk_held", self.bot.channels_state["2"])

    def test_at_most_most_held_kept(self):
        """Manager 11-16 (as C-184's MOST_HELD): a sender that goes on past the limit fills nothing: one more is not
        kept, and its Claude is told it waits on the owner's card."""
        with mock.patch.object(posts, "MOST_HELD", 3):
            for i in range(6):
                self.handled("m%d" % i)
            self.assertEqual([self.p.held[2]["text"]] + [q["text"] for q in self.p.held_more[2]], ["m2", "m3", "m4"])
            self.assertEqual(len(self.bot.channels_state["2"]["talk_held"]["posts"]), 3)
            told = [t for cid, t in self.bot.tmux.given if cid == 1]
            self.assertTrue(told and "也沒有留住：停在來回上限、等使用者決定的訊息已經有 3 則。等使用者處理 #st-0 那張卡" in told[-1], told)
            self.assertEqual(len(self.bot.tmux.calls), 1, "no new ring for it")

    def test_an_old_card_after_its_posts_went_changes_nothing(self):
        self.three_held()
        asyncio.run(self.p.on_interaction(Press(self.b, "go")))
        press = Press(self.b, "go")
        asyncio.run(self.p.on_interaction(press))
        self.assertEqual(self.given(), ["m0", "m1", "m2", "m3", "m4"], "nothing twice")


class UrgentIsNoAnswer(unittest.TestCase):
    """Bug 3: with one question open, "!!…" was eaten as its answer."""
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        p = mock.patch.object(config, "STATE", self.tmp)
        p.start()
        self.addCleanup(p.stop)
        from bot import main as botmain
        self.bot = botmain.Bot()
        self.typed, self.submitted = [], []
        test = self

        class Asks(object):
            async def typed(self, ch, text):
                test.typed.append(text)
                return True

        class Tmux(object):
            asks = Asks()

            async def submit(self, message, text, now=False, typed=None):
                test.submitted.append((text, now))
        self.bot.tmux = Tmux()
        self.bot.chan = lambda ch: {"tmux": "dc-x"}
        self.bot.needs_setup = lambda ch, st: False
        self.bot.mode = lambda st: "tmux"

    def test_urgent_goes_as_it_is(self):
        msg = types.SimpleNamespace(channel=types.SimpleNamespace(id=5), id=1)
        asyncio.run(self.bot.deliver(msg, "stop now", now=True))
        self.assertEqual((self.typed, self.submitted), ([], [("stop now", True)]))
        reacted = []

        async def add_reaction(e):
            reacted.append(e)
        msg.add_reaction = add_reaction
        asyncio.run(self.bot.deliver(msg, "B"))
        self.assertEqual((self.typed, reacted), (["B"], ["✅"]), "an ordinary message still answers it")


class ArchiveWords(unittest.TestCase):
    def test_the_terminals_button_is_ctrl_c(self):
        from bot import archive, terminal
        with open(archive.__file__, encoding="utf-8") as f:
            words = f.read()
        self.assertNotIn("按 ⏹", words)
        self.assertIn("按 Ctrl-C 停掉", words)
        with open(terminal.__file__, encoding="utf-8") as f:
            self.assertIn('label="Ctrl-C"', f.read())


if __name__ == "__main__":
    unittest.main()
