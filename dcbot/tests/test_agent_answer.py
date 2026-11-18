"""C-176 batch 2: another channel's Claude answers, for the owner, what a channel waits on (questions, plans, Claude
Code's menus), the way the owner's buttons do; never a menu counting down to Claude Code's own denial; never when the
channel turned it off; every answer and refusal said in #audit; the card says who answered."""
import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))))
from altention import testing as _testing  # noqa: E402
_testing.guard_home()

import asyncio
import json
import os
import shutil
import tempfile
import types
import unittest
from unittest import mock

from tests import tmuxguard  # noqa: F401
from bot import agentanswer, ask, tmux_bridge
from tests import test_menus
from tests.test_turn_end import Ch, Host

Q1 = {"question": "Which one?", "header": "pick", "multiSelect": False,
      "options": [{"label": "A", "description": "a"}, {"label": "B", "description": "b"}]}
Q2 = {"question": "Which ones?", "header": "many", "multiSelect": True,
      "options": [{"label": "x"}, {"label": "y"}, {"label": "z"}]}


class Asks(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        for p in (mock.patch.object(ask, "ASKS", os.path.join(self.tmp, "asks")),
                  mock.patch.object(ask, "ANSWERS", os.path.join(self.tmp, "answers"))):
            p.start()
            self.addCleanup(p.stop)
        self.ch = Ch()
        self.bridge = tmux_bridge.TmuxBridge(Host(self.ch))
        self.sender = types.SimpleNamespace(id=7, name="lead")

    def card(self):
        return self.ch.sent[-1]

    def ask(self, ask_id, questions, kind=None):
        msgs = {}
        for i, _ in enumerate(questions):
            msgs[str(i)] = asyncio.run(self.ch.send("card %d" % i)).id
        state = {"channel": str(self.ch.id), "questions": questions, "answers": {}, "messages": msgs}
        if kind:
            state["kind"] = kind
        ask.save(ask_id, state)

    def answered(self, ask_id):
        with open(os.path.join(ask.ANSWERS, ask_id + ".json")) as f:
            return json.load(f)

    def go(self, **post):
        return asyncio.run(agentanswer.answer(self.bridge, self.ch, self.sender, post))

    def test_waiting_lists_questions_plans_with_ids(self):
        self.ask("1-1", [Q1])
        self.ask("1-2", [{"question": "the plan\nstep 2"}], kind="plan")
        said = agentanswer.waiting_said(self.bridge, self.ch)
        self.assertIn("[q-1-1] 提問", said)
        self.assertIn("- A：a", said)
        self.assertIn("[q-1-2] 計畫", said)
        self.assertIn("step 2", said)

    def test_waiting_shows_the_plan_itself(self):
        """The live test of 11-11: --waiting showed only the card's question for a plan. The plan is kept with the
        record (post_plan) and shown."""
        asyncio.run(self.bridge.asks.post_plan(self.ch, {"ask_id": "1-3", "plan": "1. 建一個檔\n2. 回報完成"}))
        said = agentanswer.waiting_said(self.bridge, self.ch)
        self.assertIn("[q-1-3] 計畫", said)
        self.assertIn("    1. 建一個檔\n    2. 回報完成", said)

    def test_a_question_answered_as_the_buttons_would(self):
        self.ask("2-1", [Q1, Q2])
        ok, said = self.go(answer="q-2-1", pick=["B", "x, z"], why="the owner said B")
        self.assertTrue(ok, said)
        self.assertEqual(self.answered("2-1"), {"answers": {"Which one?": "B", "Which ones?": "x, z"}})
        self.assertIn("-# 由 #lead 的 Claude 回答", self.ch.sent[0].content)
        self.assertIsNone(self.ch.sent[0].view, "buttons gone")
        self.assertEqual(self.bridge.host.audits[-1],
                         "#c：#lead 的 Claude（自稱） 回答了提問：pick → B；many → x, z（為什麼：the owner said B）")

    def test_the_owner_answering_meanwhile_is_never_written_over(self):
        """#code-review on 0fc0847: while the Claude edits the first card, the owner answers another question. The
        owner's answer stands, whichever way it falls: three questions, the owner's is the second (the Claude is told
        that one was the owner's); two, the owner's is the last (the question is closed and handed with it)."""
        Q3 = dict(Q1, question="Third?")
        for questions, owner_qi, owner, picks, word, handed in (
                ([Q1, Q2, Q3], 1, "y", ["B", "x", "A"], "第 2 題使用者已經答了（前面已經答了：B）", None),
                ([Q1, Q2], 1, "y", ["B", "x, z"], agentanswer.GONE_ASK + "（前面已經答了：B）",
                 {"answers": {"Which one?": "B", "Which ones?": "y"}})):
            ask_id = "7-%d" % len(questions)
            self.ask(ask_id, questions)
            real, owner_done = agentanswer.edit, []

            async def edit(ch, state, qi, content, ask_id=ask_id, owner_done=owner_done, owner_qi=owner_qi, owner=owner,
                           real=real):
                if not owner_done:
                    owner_done.append(True)
                    await self.bridge.asks.settle(self.ch, ask_id, ask.load(ask_id), owner_qi, owner)  # the owner's click
                await real(ch, state, qi, content)
            with mock.patch.object(agentanswer, "edit", edit):
                ok, said = self.go(answer="q-" + ask_id, pick=picks, why="w")
            self.assertFalse(ok)
            self.assertIn(word, said)
            if handed:
                self.assertEqual(self.answered(ask_id), handed)
            else:
                self.assertEqual(ask.load(ask_id)["answers"], {"0": "B", "1": "y"}, "the owner's second stays; the third waits")

    def test_the_owners_click_a_step_behind_is_not_shown_as_sent(self):
        """#code-review on 1ad499a: the owner clicks the second question; while the card is being edited, the Claude
        answers both. What went is the Claude's; the owner's card says theirs did not go, and they are told."""
        self.ask("9-1", [Q1, Q2])
        test, told, card = self, [], []

        class Response(object):
            async def edit_message(self, content=None, view=None):
                card.append(content)
                ok, said = await agentanswer.answer(test.bridge, test.ch, test.sender, {  # the Claude, meanwhile
                    "answer": "q-9-1", "pick": ["B", "x"], "why": "w"})
                test.assertTrue(ok, said)

        class Followup(object):
            async def send(self, text, ephemeral=False):
                told.append(text)

        class Click(object):
            channel = self.ch
            response, followup = Response(), Followup()

            async def edit_original_response(self, content=None):
                card.append(content)

        async def go():
            await self.bridge.asks.record(Click(), "9-1", 1, "y")
        with mock.patch.object(agentanswer, "edit", mock.AsyncMock()):
            asyncio.run(go())
        self.assertEqual(self.answered("9-1"), {"answers": {"Which one?": "B", "Which ones?": "x"}})
        self.assertIn("→ y\n-# 這一下沒有送出：#lead 的 Claude 先回答了（x）", card[-1], "who answered first, and what")
        self.assertEqual(told, ["這一下沒有送出：#lead 的 Claude 先回答了（x）"])

    def test_a_click_on_a_question_closed_meanwhile_says_so(self):
        """Not known who answered first (the question was closed: Esc in the terminal): the plain words."""
        self.ask("9-5", [Q1])
        test, told, card = self, [], []

        class Response(object):
            async def edit_message(self, content=None, view=None):
                ask.forget("9-5")  # closed while the card is edited

        class Followup(object):
            async def send(self, text, ephemeral=False):
                told.append(text)

        class Click(object):
            channel = test.ch
            response, followup = Response(), Followup()

            async def edit_original_response(self, content=None):
                card.append(content)
        asyncio.run(self.bridge.asks.record(Click(), "9-5", 0, "A"))
        self.assertTrue(card[-1].endswith(ask.NOT_TAKEN_LINE))
        self.assertEqual(told, [ask.NOT_TAKEN])

    def test_settle_never_writes_over_an_answer_given(self):
        """settle() reads the record again: two answers to one question, the second is refused (the owner's own two
        quick clicks on two questions, each with the record it read, no longer write each other over either)."""
        self.ask("9-2", [Q1, Q2])
        stale = ask.load("9-2")
        self.assertTrue(asyncio.run(self.bridge.asks.settle(self.ch, "9-2", ask.load("9-2"), 0, "A")))
        self.assertFalse(asyncio.run(self.bridge.asks.settle(self.ch, "9-2", stale, 0, "B")))
        self.assertTrue(asyncio.run(self.bridge.asks.settle(self.ch, "9-2", stale, 1, "y")))
        self.assertEqual(self.answered("9-2"), {"answers": {"Which one?": "A", "Which ones?": "y"}})

    def test_the_owners_two_quick_clicks_both_stand(self):
        """The owner clicks two questions one after the other, the second while the first card is being edited: each
        with the record it read; both answers stand (settle() reads the record again)."""
        self.ask("9-3", [Q1, Q2])
        test = self

        def click(qi, inner=None):
            class Response(object):
                async def edit_message(self, content=None, view=None):
                    if inner is not None:
                        await inner()

            class Click(object):
                channel = test.ch
                response = Response()
            return Click()

        async def go():
            async def second():
                await self.bridge.asks.record(click(1), "9-3", 1, "y")
            await self.bridge.asks.record(click(0, second), "9-3", 0, "A")
        asyncio.run(go())
        self.assertEqual(self.answered("9-3"), {"answers": {"Which one?": "A", "Which ones?": "y"}})

    def test_typed_settled_before_its_card(self):
        """typed(): settled first, the card after (#code-review-2 on 1ad499a). Not taken (answered or closed: by then
        nothing awaits between its look and settle(), but whatever the reason): the card is left as it is, the text
        goes to Claude as an ordinary message, and the owner is told."""
        self.ask("9-4", [Q1])
        card = self.ch.sent[0]
        with mock.patch.object(self.bridge.asks, "settle", mock.AsyncMock(return_value=False)):
            self.assertFalse(asyncio.run(self.bridge.asks.typed(self.ch, "B please")))
        self.assertEqual(card.content, "card 0", "the card shows no answer that did not go")
        self.assertEqual(self.ch.sent[-1].content, ask.NOT_TAKEN_TYPED)
        self.assertTrue(asyncio.run(self.bridge.asks.typed(self.ch, "B please")))
        self.assertIn("→ B please", card.content)

    def test_closed_partway_nothing_more_answered(self):
        """Read again for each question (manager, #code-review-2 on 0fc0847): cancelled at the terminal while the
        Claude is between its answers, the rest is not written, nothing is handed, and it is told what it did answer."""
        self.ask("8-1", [Q1, Q2])
        real = agentanswer.edit

        async def edit(ch, state, qi, content):
            ask.forget("8-1")  # Esc in the terminal: the question is gone (C-152)
            await real(ch, state, qi, content)
        with mock.patch.object(agentanswer, "edit", edit):
            ok, said = self.go(answer="q-8-1", pick=["B", "x"], why="w")
        self.assertEqual((ok, said), (False, "沒送：" + agentanswer.GONE_ASK + "（前面已經答了：B）"))
        self.assertFalse(os.path.exists(os.path.join(ask.ANSWERS, "8-1.json")), "nothing handed to Claude Code")
        self.assertIsNone(ask.load("8-1"))

    def test_text_for_the_one_left(self):
        self.ask("3-1", [Q1])
        self.assertTrue(self.go(answer="q-3-1", text="neither, use C", why="w")[0])
        self.assertEqual(self.answered("3-1"), {"answers": {"Which one?": "neither, use C"}})

    def test_refused_and_said_why(self):
        self.ask("4-1", [Q1, Q2])
        for post, word in ((dict(pick=["C", "x"]), "不是第 1 題的選項"), (dict(pick=["A"]), "--pick 要給 2 個"),
                           (dict(text="free"), "--text 只用在只剩一題")):
            ok, said = self.go(answer="q-4-1", why="w", **post)
            self.assertFalse(ok)
            self.assertIn(word, said)
        self.assertIsNotNone(ask.load("4-1"), "nothing answered")
        self.assertTrue(all("沒送" in a for a in self.bridge.host.audits))

    def test_already_answered_closed_or_unknown(self):
        ok, said = self.go(answer="q-9-9", pick=["A"], why="w")
        self.assertEqual((ok, said), (False, "沒送：" + agentanswer.GONE_ASK))
        ok, said = self.go(answer="zz", why="w")
        self.assertIn("沒有這個編號", said)

    def test_a_plan_approved_or_sent_back(self):
        self.ask("5-1", [{"question": "plan"}], kind="plan")
        self.assertTrue(self.go(answer="q-5-1", approve=True, why="owner said go")[0])
        self.assertEqual(self.answered("5-1"), {"approve": True})
        self.assertTrue(self.card().content.startswith("✅ **計畫已核准**"))
        self.assertIn("由 #lead 的 Claude 回答", self.card().content)
        self.assertEqual(self.bridge.host.audits[-1], "#c：#lead 的 Claude（自稱） 核准了計畫（為什麼：owner said go）")
        self.ask("5-2", [{"question": "plan"}], kind="plan")
        self.assertTrue(self.go(answer="q-5-2", revise="smaller steps", why="w")[0])
        self.assertIn("smaller steps", self.answered("5-2")["reason"])
        self.assertFalse(self.go(answer="q-5-1", approve=True, why="w")[0], "dealt with already")

    def test_off_no_sender_or_itself(self):
        self.ask("6-1", [Q1])
        self.bridge.host.chan(self.ch)[agentanswer.FLAG] = False
        self.assertEqual(self.go(answer="q-6-1", pick=["A"], why="w"), (False, "沒送：" + agentanswer.OFF))
        del self.bridge.host.chan(self.ch)[agentanswer.FLAG]
        self.assertEqual(asyncio.run(agentanswer.answer(self.bridge, self.ch, None, {"answer": "q-6-1", "pick": ["A"]})),
                         (False, "沒送：" + agentanswer.NO_SENDER))
        self.assertEqual(asyncio.run(agentanswer.answer(self.bridge, self.ch, self.ch, {"answer": "q-6-1", "pick": ["A"]})),
                         (False, "沒送：" + agentanswer.SELF))
        self.assertIsNotNone(ask.load("6-1"))
        self.assertEqual(len(self.bridge.host.audits), 3, "each refusal said")


class Menus(unittest.TestCase):
    """A menu answered through menus.act(): the same look at the screen again as the owner's button (test_menus'
    world: its setUp, screens and post, not its tests)."""
    setUp, screens, post = test_menus.Pressed.setUp, test_menus.Pressed.screens, test_menus.Pressed.post

    def answer(self, token, views, **post):
        self.views = list(views)
        sender = types.SimpleNamespace(id=7, name="lead")
        with self.screens(), self.mock.patch.object(self.asyncio, "sleep", self.mock.AsyncMock()):
            return self.asyncio.run(agentanswer.answer(self.bridge, self.ch, sender, dict(post, answer="m-" + token)))

    def test_a_key_pressed_and_said(self):
        token = self.post()
        ok, said = self.answer(token, [self.first, "done\n❯ "], key="1", why="owner allowed")
        self.assertTrue(ok, said)
        self.assertEqual(self.pressed, ["1"])
        self.assertIn("（由 #lead 的 Claude 回答）", self.edits[-1][0])
        self.assertEqual(self.bridge.host.audits[-1], "#c：#lead 的 Claude（自稱） 在選單選了 1（為什麼：owner allowed）")

    def test_the_screen_changed_nothing_pressed(self):
        token = self.post()
        ok, said = self.answer(token, ["something else\n❯ "], key="1", why="w")
        self.assertFalse(ok)
        self.assertIn("畫面已經變了", said)
        self.assertEqual(self.pressed, [])

    def test_a_countdown_menu_is_the_owners(self):
        token = self.post()
        self.menus.open[token]["deadline"] = 9999999999
        ok, said = self.answer(token, [self.first], key="1", why="w")
        self.assertEqual((ok, said), (False, "沒送：" + agentanswer.COUNTDOWN))
        self.assertEqual(self.pressed, [])

    def test_claude_codes_last_check_is_the_owners_however_worded(self):
        """Manager 11-11, after 2.1.322 reworded the countdown: refused on the real 2.1.302 and 2.1.322 screens, on one
        whose warning row only starts as theirs do (a wording to come), and on a "Dangerous" one with no countdown."""
        from altention.tests.connectors.claude_code import samples
        new = samples.extra("P", "countdown-rig", version="2.1.322").replace(
            "Claude Code will automatically block this request in 1:53, to avoid holding up an unattended session",
            "Claude Code will turn this request down in a moment unless someone answers")
        no_row = "\n".join(r for r in samples.extra("P", "countdown-rig", version="2.1.322").split("\n") if "⚠" not in r)
        for name, view in (("2.1.302", samples.extra("P", "countdown-colour-120x50-a", version="2.1.302")),
                           ("2.1.322", samples.extra("P", "countdown-rig", version="2.1.322")),
                           ("a wording to come", new), ("Dangerous, no countdown row", no_row)):
            with self.subTest(name):
                self.menus.open.clear()
                self.pressed.clear()
                self.first = view
                token = self.post()
                ok, said = self.answer(token, [view], key="1", why="w")
                self.assertEqual((ok, said), (False, "沒送：" + agentanswer.COUNTDOWN))
                self.assertEqual(self.pressed, [])

    def test_waiting_lists_the_menu(self):
        token = self.post()
        said = agentanswer.waiting_said(self.bridge, self.ch)
        self.assertIn("[m-%s] 終端機選單" % token, said)
        self.assertIn("1. ", said)

    def test_a_panel_is_not_listed_nor_pressed(self):
        """#code-review on 0fc0847: /model's panel (its own keys, no screen check for Enter or text) is the owner's."""
        self.menus.open["p1"] = {"channel": self.ch.id, "title": "/model", "actions": [("enter", "select")], "msg": 5}
        self.assertNotIn("m-p1", agentanswer.waiting_said(self.bridge, self.ch))
        ok, said = self.answer("p1", ["panel"], key="enter", why="w")
        self.assertEqual((ok, said), (False, "沒送：" + agentanswer.PANEL))
        self.assertEqual(self.pressed, [])

    def test_a_key_not_offered(self):
        token = self.post()
        ok, said = self.answer(token, [self.first], key="9", why="w")
        self.assertFalse(ok)
        self.assertIn("沒有選項 9", said)


if __name__ == "__main__":
    unittest.main()
