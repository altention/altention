"""C-205: altention dc post --delete <id…> --why (bot/deleting.py): any message of a channel, saved whole in #audit
first (masked; its JSON as a file), one by one, what the bot's state holds of it let go, someone else's said in the
channel without a mention; never what cannot be read or saved, never in #audit, never a thread's first message."""
import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))))
from altention import testing as _testing  # noqa: E402
_testing.guard_home()  # tests never touch the real home (altention/testing.py)
import asyncio
import json
import os
import types
import unittest
from unittest import mock

import discord

from bot import ask, deleting, ledger
from tests import tmuxguard  # noqa: F401  (no test reaches a real tmux server)
from tests.test_post_json import Base

BOT, OWNER, OTHER = 5, 42, 77
KEY = "sk-ant-" + "a" * 30  # (as tests/test_masking.py's: a made-up key of the shape masking knows)


def answer(status, code, text="x"):
    return types.SimpleNamespace(status=status, reason=text)


def raw(mid, cid, author=BOT, content="hi", **kw):
    return dict({"id": str(mid), "channel_id": str(cid), "content": content, "timestamp": "2026-11-20T01:02:03+00:00",
                 "author": {"id": str(author), "username": "u%d" % author, "bot": author == BOT},
                 "embeds": [], "components": [], "attachments": [], "flags": 0}, **kw)


class Delete(Base):
    def setUp(self):
        super().setUp()
        self.bot.user = types.SimpleNamespace(id=BOT)
        self.saved, self.save_fails = [], False

        async def keep_in_audit(text, name, data):
            if self.save_fails:
                return False
            self.saved.append((text, name, json.loads(data.decode("utf-8"))))
            return True
        self.bot.keep_in_audit = keep_in_audit
        self.messages = {}  # path -> a payload, or an exception the GET raises
        http, get = self.bot.http, self.bot.http.request

        async def request(route, **kw):
            path = route.url[len(route.BASE):]
            if route.method == "GET" and path in self.messages:
                got = self.messages[path]
                if callable(got):
                    got = await got()
                if isinstance(got, BaseException):
                    raise got
                return got
            if route.method == "GET" and "/messages/" in path:
                raise discord.NotFound(answer(404, 10008), {"code": 10008, "message": "Unknown Message"})
            return await get(route, **kw)
        http.request = request
        self.fails = {}  # message id -> exceptions its deletes raise, in turn

        def partial(chan):
            def get(mid):
                class Partial(object):
                    id = mid

                    async def delete(self):
                        left = self.fails_of()
                        if left:
                            raise left.pop(0)
                        chan.deleted = getattr(chan, "deleted", []) + [mid]

                    def fails_of(self, me=self):
                        return fails.get(mid)
                return Partial()
            return get
        fails = self.fails
        for c in (self.a, self.b, self.t):
            c.get_partial_message = partial(c)

    def has(self, cid, mid, **kw):
        self.messages["/channels/%d/messages/%d" % (cid, mid)] = raw(mid, cid, **kw)

    def delete(self, cid, *mids, sender="1", why="寫錯了", **kw):
        kw.setdefault("wait", 60)
        return self.post(channel=str(cid), delete_messages=[str(m) for m in mids], why=why, from_channel=sender, **kw)

    def test_the_bots_own_unledgered_saved_then_taken_away_no_line(self):
        self.has(2, 600)  # (not in the ledger: a reply, a notice, anything of the bot's)
        ledger.note(2, 601, "card", frm=None)
        self.has(2, 601, content="卡片")
        got = self.delete(2, 600, 601)
        self.assertEqual((got["ok"], got["text"]), (True, "刪掉了：600、601"))
        self.assertEqual(self.b.deleted, [600, 601])
        self.assertIsNone(ledger.entry(601, 2), "its entry goes too")
        self.assertEqual(len(self.saved), 2)
        text, name, data = self.saved[0]
        self.assertEqual(name, "message-600.json")
        self.assertIn("🗑️ #lab 的訊息 600 要被刪，先存在這裡（#st-1 的 Claude 要刪；#st-1（查證過）；理由：寫錯了）", text)
        self.assertIn("作者：u5（5）（機器人）　時間：2026-11-20T01:02:03+00:00", text)
        self.assertIn("/2/600", text)
        self.assertEqual(data["content"], "hi")
        self.assertEqual(self.b.sent, [], "the bot's own: #audit only, no line in the channel")
        self.assertEqual(self.bot.audits[-1], "[altention dc post --delete] #lab：刪了 2 則：600、601（#st-1（查證過）；理由：寫錯了）")

    def test_the_owners_and_others_one_line_each_author_nobody_rung(self):
        self.has(2, 610, author=OWNER)
        self.has(2, 611, author=OWNER)
        self.has(2, 612, author=OTHER)
        self.has(2, 613)
        got = self.delete(2, 610, 611, 612, 613, why="清掉測試訊息")
        self.assertTrue(got["ok"], got)
        self.assertEqual(self.b.deleted, [610, 611, 612, 613])
        self.assertEqual(self.b.sent, ["-# 🗑️ #st-1 的 Claude 刪了 2 則 <@42> 的訊息：清掉測試訊息\n"
                                       "-# 🗑️ #st-1 的 Claude 刪了 1 則 <@77> 的訊息：清掉測試訊息"])
        self.assertEqual(self.b.how[-1]["mentions"].to_dict(), discord.AllowedMentions.none().to_dict())

    def test_masked_in_audit_and_in_its_file(self):
        self.has(2, 620, author=OWNER, content="token " + KEY,
                 embeds=[{"title": "t " + KEY, "description": KEY, "fields": [{"name": "n", "value": KEY}]}],
                 components=[{"type": 17, "components": [{"type": 10, "content": "v2 " + KEY}]}],
                 attachments=[{"filename": "a.png", "size": 12, "url": "https://cdn.example/a.png"}])
        self.assertTrue(self.delete(2, 620)["ok"])
        text, _, data = self.saved[0]
        self.assertNotIn(KEY, text)
        self.assertNotIn(KEY, json.dumps(data))
        self.assertIn("token [REDACTED]", text)
        self.assertIn("附件：a.png（12 bytes）https://cdn.example/a.png", text)
        self.assertEqual(data["components"][0]["components"][0]["content"], "v2 [REDACTED]")
        self.assertEqual(data["attachments"][0]["url"], "https://cdn.example/a.png", "what is not secret stays")

    def test_not_read_not_saved_not_taken_away(self):
        self.has(2, 630)
        self.messages["/channels/2/messages/631"] = discord.HTTPException(answer(503, 0), {"code": 0, "message": "x"})
        self.messages["/channels/2/messages/632"] = discord.Forbidden(answer(403, 50001), {"code": 50001, "message": "x"})

        async def slow():
            await asyncio.sleep(1)
        self.messages["/channels/2/messages/633"] = slow
        with mock.patch.object(deleting, "FETCH_SECS", 0.05):
            got = self.delete(2, 631, 632, 633)
        self.assertFalse(got["ok"])
        for mid in (631, 632, 633):
            self.assertIn("沒有刪 %d：讀不到這則，沒有刪" % mid, got["text"])
        self.save_fails = True
        got = self.delete(2, 630)
        self.assertEqual((got["ok"], got["text"]), (False, "沒有刪 630：存不進 #audit，沒有刪"))
        self.assertFalse(hasattr(self.b, "deleted"), "nothing taken away")

    def test_gone_already_is_no_error_and_lets_go(self):
        ledger.note(2, 640, "post", frm="channel:1")
        got = self.delete(2, 640)
        self.assertEqual((got["ok"], got["text"]), (True, "已經不在了（之前就刪掉了）：640"))
        self.assertIsNone(ledger.entry(640, 2))
        self.has(2, 641)
        self.fails[641] = [discord.NotFound(answer(404, 10008), {"code": 10008, "message": "Unknown Message"})]
        self.assertEqual(self.delete(2, 641)["text"], "已經不在了（之前就刪掉了）：641")

    def test_never_in_audit_nor_a_threads_head_nor_another_channels(self):
        self.bot.audit_ch.id = 2
        got = self.delete(2, 650)
        self.assertEqual((got["ok"], got["text"]), (False, "altention dc post --delete：沒有刪：#audit 是紀錄，不能刪"))
        self.bot.audit_ch.id = 99
        self.has(2, 651, flags=32)
        self.has(3, 3)  # (a thread's own first message: its id is the thread's)
        self.assertIn("這則開了討論串", self.delete(2, 651)["text"])
        self.assertIn("這則開了討論串", self.delete(3, 3)["text"])
        ledger.note(1, 652, "post", frm="channel:1")
        self.assertIn("這則不在這個頻道", self.delete(2, 652)["text"])
        self.assertIsNotNone(ledger.entry(652, 1), "asked of the wrong channel: its entry stays")
        self.assertEqual(self.saved, [])

    def test_ids_checked_by_the_bot_too(self):
        got = self.delete(2, *range(700, 721))
        self.assertEqual((got["ok"], got["text"]), (False, "altention dc post --delete：沒有刪：一次最多 20 則，請分批"))
        self.assertIn("要帶 --why", self.delete(2, 700, why="")["text"])
        ledger.note(2, 722, "post", frm="channel:1")
        got = self.post(channel="2", delete_message="722", why="w", from_channel="1", wait=30)  # (an older dc post)
        self.assertEqual(got["text"], "已經不在了（之前就刪掉了）：722")

    def test_not_found_and_not_on_record_said_as_maybe_another_channel(self):
        """#code-review-2 low 1 on a830dc83: Discord's 404 for a message of another channel is its 404 for a deleted
        one, and the ledger holds only the bot's: someone's message asked of the wrong channel is not said as gone."""
        got = self.delete(2, 723)
        self.assertEqual(got["text"], "這個頻道裡找不到（可能已經刪了，或在別的頻道；在別的頻道就對那個頻道再下一次）：723")
        ledger.note(2, 724, "post", frm="channel:1")
        got = self.delete(2, 723, 724)
        self.assertIn("已經不在了（之前就刪掉了）：724", got["text"])
        self.assertNotIn("724", got["text"].split("\n")[1])

    def test_an_open_menus_card_its_menu_told(self):
        """#code-review's must-fix on a830dc83: the card of a menu still open (a permission, a plan dialog): the
        menus let it go and post anew (tests/test_menus_restored.py), and #audit says what it was."""
        asked = []

        class Menus(object):
            async def card_deleted(me, ch, mid):
                asked.append((ch.id, mid))
                return "要用 Bash 嗎" if mid == "655" else None
        self.bot.tmux.menus = Menus()
        self.has(2, 655)
        self.has(2, 656)
        self.assertTrue(self.delete(2, 655, 656)["ok"])
        self.assertEqual(asked, [(2, "655"), (2, "656")])
        self.assertIn("655（還開著的選單卡（要用 Bash 嗎）", self.bot.audits[-1])
        self.assertNotIn("656（還開著", self.bot.audits[-1])

    def test_a_thread_field_alone_is_a_threads_head(self):
        self.has(2, 653, thread={"id": "900"})
        self.assertIn("這則開了討論串", self.delete(2, 653)["text"])
        self.assertEqual(self.saved, [])

    def test_many_authors_long_reason_split_under_2000(self):
        for i in range(12):
            self.has(2, 730 + i, author=1000 + i)
        sent = []
        real = deleting.deliver.deliver

        async def deliver(ch, content=None, **kw):
            sent.append(content)
            return await real(ch, content, **kw)
        with mock.patch.object(deleting.deliver, "deliver", deliver):
            got = self.delete(2, *range(730, 742), why="長" * 300)
        self.assertTrue(got["ok"], got)
        self.assertGreater(len(sent), 1)
        self.assertTrue(all(len(s) <= 2000 for s in sent))
        self.assertEqual(sum(s.count("🗑️") for s in sent), 12)

    def test_429_waited_out_then_taken_away_403_said_as_the_permission(self):
        self.has(2, 660)
        self.has(2, 661)
        self.fails[660] = [discord.HTTPException(answer(429, 0), {"code": 0, "message": "rate limited"})]
        self.fails[661] = [discord.Forbidden(answer(403, 50013), {"code": 50013, "message": "Missing Permissions"})]
        with mock.patch.object(deleting, "retry_after", return_value=0.01):
            got = self.delete(2, 660, 661)
        self.assertEqual(self.b.deleted, [660])
        self.assertIn("刪掉了：660", got["text"])
        self.assertIn("沒有刪 661：機器人沒有「管理訊息」權限，沒有刪", got["text"])
        self.has(2, 662)
        self.fails[662] = [discord.HTTPException(answer(429, 0), {"code": 0, "message": "x"}) for _ in range(5)]
        with mock.patch.object(deleting, "retry_after", return_value=0.01):
            self.assertIn("速率限制", self.delete(2, 662)["text"])

    def test_the_deadline_leaves_the_rest_untouched_and_says_so(self):
        now = [0.0]

        async def late():
            now[0] += 60
            return raw(670, 2)
        self.messages["/channels/2/messages/670"] = late
        self.has(2, 671)
        self.has(2, 672)
        with mock.patch.object(deleting.time, "monotonic", lambda: now[0]):
            got = self.delete(2, 670, 671, 672, wait=40)
        self.assertFalse(got["ok"])
        self.assertIn("時間不夠，沒輪到：671、672（沒有動，請再送一次）", got["text"])
        self.assertIn("時間不夠沒輪到 2 則：671、672", self.bot.audits[-1])
        self.assertNotIn(671, getattr(self.b, "deleted", []))

    def test_the_deadline_from_answer_wait_as_dcctl_leaves_the_post(self):
        """Rig drill 11-20: dcctl pops "wait" before the spool, so 20 at once stopped at 30 - MARGIN seconds while the
        sender waited 150. The post as dcctl writes it: no "wait", "answer_wait" from altention dc post."""
        now = [0.0]

        async def late():
            now[0] += 60
            return raw(675, 2)
        self.messages["/channels/2/messages/675"] = late
        self.has(2, 676)
        with mock.patch.object(deleting.time, "monotonic", lambda: now[0]):
            got = self.post(channel="2", delete_messages=["675", "676"], why="w", from_channel="1", answer_wait=150)
        self.assertEqual((got["ok"], got["text"]), (True, "刪掉了：675、676"))

    def test_board_asking_notice_and_mention_let_go(self):
        st2 = self.bot.channels_state["2"]
        st2["boards"], st2["boards_v2"] = {"本週": [680, 681], "單則": [682]}, {"本週": [True, False], "單則": [True]}
        st2["calls"] = {"discuss:9": [683, 1e18, False, "要跟你討論"]}
        self.bot.channels_state["1"]["asking"] = {"q1": [2, 684]}
        for mid in (681, 682, 683, 684):
            self.has(2, mid)
        got = self.delete(2, 681, 682, 683, 684)
        self.assertTrue(got["ok"], got)
        self.assertEqual((st2["boards"], st2["boards_v2"]), ({"本週": [680]}, {"本週": [True]}))
        self.assertEqual(st2["calls"]["discuss:9"][:3], [None, 1e18, True], "over: never edited, not mentioned again")
        self.assertEqual(self.bot.channels_state["1"]["asking"], {})
        line = self.bot.audits[-1]
        self.assertIn("681（看板「本週」的一部分", line)
        self.assertIn("683（@ 使用者的「等你」那行（discuss:9），標成結束）", line)
        self.assertIn("684（「⏸️ 在等你」的通知（q1），紀錄拿掉了）", line)
        self.assertIn("#st-1（查證過）；理由：寫錯了", line, "who deleted it and why")


class DeletedCard(Base):
    """A question or plan still open whose card is taken away: closed, its Claude told why (manager 11-20)."""
    def setUp(self):
        super().setUp()
        self.bot.user = types.SimpleNamespace(id=BOT)

        async def keep_in_audit(text, name, data):
            return True
        self.bot.keep_in_audit = keep_in_audit
        for p in (mock.patch.object(ask, "ASKS", os.path.join(self.tmp, "asks")),
                  mock.patch.object(ask, "ANSWERS", os.path.join(self.tmp, "answers"))):
            p.start()
            self.addCleanup(p.stop)
        self.asker = ask.Asker(self.bot.audit)
        self.done = []

        async def on_done(ch, ask_id, how):
            self.done.append((ch.id, ask_id, how))
        self.asker.on_done = on_done
        self.bot.tmux.asks = self.asker
        get = self.bot.http.request

        async def request(route, **kw):
            path = route.url[len(route.BASE):]
            if route.method == "GET" and path.startswith("/channels/2/messages/"):
                return raw(path.rsplit("/", 1)[1], 2)
            return await get(route, **kw)
        self.bot.http.request = request
        self.edits = []

        def partial(mid):
            chan = self.b

            class Partial(object):
                id = mid

                async def delete(self):
                    chan.deleted = getattr(chan, "deleted", []) + [mid]

                async def edit(me, **kw):
                    self.edits.append((mid, kw.get("content")))
            return Partial()
        self.b.get_partial_message = partial

    def test_closed_and_its_claude_told_the_card_was_deleted(self):
        qs = [{"question": "選哪個？", "header": "選", "options": [{"label": "A"}]},
              {"question": "第二題", "header": "二", "options": [{"label": "B"}]}]
        ask.save("ab12", {"channel": "2", "questions": qs, "answers": {}, "messages": {"0": 690, "1": 691}, "ts": 1, "host": None})
        got = self.post(channel="2", delete_messages=["690"], why="卡片貼錯了", from_channel="1", wait=60)
        self.assertTrue(got["ok"], got)
        self.assertIsNone(ask.load("ab12"), "closed")
        with open(os.path.join(self.tmp, "answers", "ab12.json")) as f:
            reason = json.load(f)["reason"]
        self.assertIn("card of this question was deleted by #st-1 的 Claude (reason: 卡片貼錯了)", reason)
        self.assertIn("The user did not ignore it", reason)
        self.assertEqual(self.done, [(2, "ab12", "已取消（卡片被刪了）")])
        self.assertEqual(self.edits, [(691, ask.answered_text(qs[1], ask.CARD_DELETED_HERE, "⌛"))], "the other card says so")
        self.assertIn("這張卡片被刪了", self.edits[0][1])
        self.assertIn("690（提問或計畫的卡片（ab12），提問已取消", self.bot.audits[-1])

    def test_a_card_of_no_open_question_closes_nothing(self):
        ask.save("cd34", {"channel": "2", "questions": [{"question": "q"}], "answers": {}, "messages": {"0": 692},
                          "ts": 1, "host": None})
        self.assertTrue(self.post(channel="2", delete_messages=["693"], why="w", from_channel="1", wait=60)["ok"])
        self.assertIsNotNone(ask.load("cd34"))


if __name__ == "__main__":
    unittest.main()
