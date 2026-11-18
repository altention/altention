"""C-172a P1a: the ledger of the bot's messages (bot/ledger.py) and the readers that ask it first (frames.of, the
boards). Acceptance 3–5 of instance docs/refactor/c172a-design.md: a reply's own "-#" small print with "×3 · 10 秒"
stays; every part of a board is told; with the ledger gone, everything still works, by guessing."""
import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))))
from altention import testing as _testing  # noqa: E402
_testing.guard_home()  # tests never touch the real home (altention/testing.py)
import json
import os
import tempfile
import types
import unittest
from unittest import mock

from bot import frames, ledger, posts, standby
from tests import tmuxguard  # noqa: F401  (no test reaches a real tmux server)


class Ledger(unittest.TestCase):
    def setUp(self):
        self.file = os.path.join(tempfile.mkdtemp(), "ledger.json")
        p = mock.patch.dict(os.environ, {"DCBOT_LEDGER": self.file})
        p.start()
        self.addCleanup(p.stop)
        ledger.reset()
        self.addCleanup(ledger.reset)

    def on_disk(self):
        ledger.save()
        with open(self.file, encoding="utf-8") as f:
            return json.load(f)

    def test_noted_read_and_replaced(self):
        ledger.note(10, 100, "post", frm="#st-1", framed=("card",), at=5)
        self.assertEqual(ledger.entry(100, 10), {"from": "#st-1", "as": None, "intent": "post", "key": None,
                                             "framed": ["card"], "at": 5})
        self.assertIsNone(ledger.entry(100, 11), "another channel's")
        self.assertIsNone(ledger.entry(100, None), "the channel not known: never guessed")
        ledger.note(10, 100, "board", key="x", framed=(), at=6)  # (edited: the entry of that id replaced; who it
        self.assertEqual(self.on_disk()["channels"]["10"], [["100", {"from": "#st-1", "as": None, "intent": "board",  # is from not said: kept
                                                                     "key": "x", "framed": [], "at": 6}]])
        ledger.note(10, 101, "nonsense")
        ledger.note(None, 102, "post")
        self.assertIsNone(ledger.entry(101, 10))
        self.assertIsNone(ledger.entry(102, None))

    def test_an_edit_without_a_key_keeps_the_one_on_record(self):
        """#code-review-2 on 8be878b6, manager 11-16: a menu's or a question's card edited only to take its buttons off
        (no key handed) keeps its menu:/ask: key; a new key replaces it."""
        ledger.note(10, 100, "card", key="menu:t1", at=5)
        ledger.note(10, 100, "card", at=6)
        self.assertEqual(ledger.entry(100, 10)["key"], "menu:t1")
        ledger.note(10, 100, "card", key="ask:9", at=7)
        self.assertEqual(ledger.entry(100, 10)["key"], "ask:9")
        ledger.note(10, 101, "card", at=8)
        self.assertIsNone(ledger.entry(101, 10)["key"], "nothing on record: none")

    def test_an_edit_that_says_nothing_keeps_the_rest_on_record(self):
        """Both reviews on 99392598, manager 11-16: an edit that says no frm, as_ or framed keeps the entry's own;
        framed=() clears it."""
        ledger.note(10, 100, "reply", frm="#st-1", as_="lead", framed=("status",), at=5)
        ledger.note(10, 100, "reply", at=6)  # (📂 added under it)
        e = ledger.entry(100, 10)
        self.assertEqual((e["from"], e["as"], e["framed"]), ("#st-1", "lead", ["status"]))
        ledger.note(10, 100, "reply", framed=(), at=7)
        self.assertEqual(ledger.entry(100, 10)["framed"], [], "said: none")
        ledger.note(10, 101, "reply", at=8)
        self.assertEqual(ledger.entry(101, 10)["framed"], [], "nothing on record: none")

    def test_kept_per_channel_and_in_all(self):
        with mock.patch.object(ledger, "PER_CHANNEL", 3), mock.patch.object(ledger, "TOTAL", 5):
            for i in range(5):
                ledger.note(1, i, "reply", at=i)
            self.assertEqual([r[0] for r in self.on_disk()["channels"]["1"]], ["2", "3", "4"])
            for i in range(10, 13):
                ledger.note(2, i, "reply", at=i)
            data = self.on_disk()["channels"]
            self.assertEqual(sum(len(v) for v in data.values()), 5, "the oldest of all went first")
            self.assertEqual([r[0] for r in data["2"]], ["10", "11", "12"])

    def test_pinned_kept_beyond_the_limits_up_to_pinned_keep(self):
        """C-172a P1c (manager 11-16): a pinned entry is kept however many come after it, in its channel and in all;
        PINNED_KEEP a channel at most (the newest); unpinned, it goes as any other; an edit keeps the pin."""
        with mock.patch.object(ledger, "PER_CHANNEL", 3), mock.patch.object(ledger, "TOTAL", 4), \
                mock.patch.object(ledger, "PINNED_KEEP", 2):
            ledger.note(1, 0, "post", frm="w", at=0)
            self.assertTrue(ledger.pin(1, 0, True))
            self.assertFalse(ledger.pin(1, 99, True), "not on record: nothing to keep")
            for i in range(1, 6):
                ledger.note(1, i, "reply", at=i)
            self.assertEqual([r[0] for r in self.on_disk()["channels"]["1"]], ["0", "3", "4", "5"])
            ledger.note(1, 0, "post", at=9)  # (an edit)
            self.assertTrue(ledger.entry(0, 1)["pinned"])
            ledger.note(1, 0, "post", framed=("status",), at=10)  # (an edit that changes what is on record: #code-review-2)
            self.assertEqual((ledger.entry(0, 1)["framed"], ledger.entry(0, 1).get("pinned")), (["status"], True))
            for i in range(10, 13):
                ledger.note(2, i, "reply", at=i)
            data = self.on_disk()["channels"]
            self.assertIn("0", [r[0] for r in data["1"]], "the oldest of all go first, the pinned aside")
            self.assertEqual(sum(1 for v in data.values() for r in v if not r[1].get("pinned")), 4)
            for i in (6, 7):
                ledger.note(1, i, "post", at=20 + i)
                ledger.pin(1, i, True)
            self.assertEqual([r[0] for r in self.on_disk()["channels"]["1"] if r[1].get("pinned")], ["6", "7"],
                             "PINNED_KEEP a channel: the oldest pin goes")
            ledger.pin(1, 7, False)
            self.assertNotIn("pinned", ledger.entry(7, 1))

    def test_gone_or_broken_is_nothing_on_record(self):
        ledger.note(1, 7, "reply", framed=("status",))
        ledger.save()
        os.remove(self.file)
        ledger.reset()
        self.assertIsNone(ledger.entry(7, 1))
        with open(self.file, "w") as f:
            f.write("{not json")
        ledger.reset()
        self.assertIsNone(ledger.entry(7, 1))
        ledger.note(1, 8, "reply")  # (writes on all the same)
        self.assertEqual(list(self.on_disk()["channels"]["1"][0][:1]), ["8"])

    def test_a_deleted_channel_and_the_standby(self):
        ledger.note(1, 7, "reply")
        ledger.note(2, 8, "reply")
        ledger.forget_channel(1)
        self.assertEqual(sorted(self.on_disk()["channels"]), ["2"])
        self.assertIn("ledger.json", standby.SYNCED)

    def test_a_post_is_noted_with_who_sent_it(self):
        """deliver() notes it (C-172a P1b), with the sender handling the post found (posts.sender_said())."""
        import asyncio
        from bot import deliver
        got = {"messages": [], "why": None, "from": "#st-1"}
        token = posts.SENT.set(got)
        self.addCleanup(posts.SENT.reset, token)

        class Ch(object):
            id = 9

            async def send(self, *a, **kw):
                return types.SimpleNamespace(id=55, channel=self)
        msg = asyncio.run(deliver.deliver(Ch(), "x", intent="post", frm=posts.sender_said(), framed=("card",)))
        self.assertEqual(posts.sent(msg), msg)
        self.assertEqual((ledger.entry(55, 9)["from"], ledger.entry(55, 9)["framed"]), ("#st-1", ["card"]))
        self.assertEqual(got["messages"], [msg], "its receipt as before")


class Frames(unittest.TestCase):
    def test_a_reply_on_record_keeps_its_own_small_print(self):
        """Acceptance 3: "-# …×3、10 秒" written by the agent is not taken for the status line."""
        text = "結果如下\n-# 跑了 Bash×3 · 10 秒（我自己的註）"
        self.assertEqual(frames.of(text, by_bot=True, entry={"intent": "reply", "framed": []}),
                         {"kind": frames.REPLY, "text": text, "registered": True})
        foot = text + "\n-# Bash×1 · 3 秒 · context 5%"
        self.assertEqual(frames.of(foot, by_bot=True, entry={"intent": "reply", "framed": ["status"]})["text"], text)

    def test_every_part_of_a_board(self):
        """Acceptance 4: the parts after the first have no heading; the ledger names the board of each."""
        first = frames.of("📌 **機器**\n-# 更新於 10:00\n第一段", by_bot=True,
                          entry={"intent": "board", "key": "機器", "framed": ["board_head"]})
        later = frames.of("第二段", by_bot=True, entry={"intent": "board", "key": "機器", "framed": []})
        self.assertEqual((first["kind"], first["name"], first["text"], first["updated"]), (frames.BOARD, "機器", "第一段", "10:00"))
        self.assertEqual((later["kind"], later["name"], later["text"]), (frames.BOARD, "機器", "第二段"))
        new_looks = frames.of("## 機器\n第一段", by_bot=True, entry={"intent": "board", "key": "機器", "framed": []})
        self.assertEqual(new_looks["name"], "機器", "told by the ledger, not by 📌")

    def test_not_on_record_is_guessed_and_says_so(self):
        """Acceptance 5: with no ledger, as before, marked; a caller that did not ask: as before, unmarked."""
        guessed = frames.of("📌 **機器**\n-# 更新於 10:00\n x", by_bot=True, entry=None)
        self.assertEqual((guessed["kind"], guessed.get("guessed")), (frames.BOARD, True))
        self.assertNotIn("guessed", frames.of("📌 **機器**\n x", by_bot=True))
        self.assertNotIn("guessed", frames.of("hi", by_owner=True, entry=None), "the owner's: nothing to guess")

    def test_posts_and_notices_on_record(self):
        card = frames.of("", [{"author": "📨 來自 #st-1", "description": "hi"}], by_bot=True,
                         entry={"intent": "post", "from": "#st-1", "framed": ["card"]})
        self.assertEqual((card["kind"], card["sender"], card["text"], card["registered"]), (frames.RELAYED, "#st-1", "hi", True))
        own = frames.of("我自己貼的", by_bot=True, entry={"intent": "post", "from": "#st-1", "framed": []})
        self.assertEqual((own["kind"], own["sender"], own["text"]), (frames.RELAYED, "#st-1", "我自己貼的"))
        note = frames.of("🔔 **[cron]** 好了", by_bot=True, entry={"intent": "notify", "from": "cron", "framed": ["notice_head"]})
        self.assertEqual((note["kind"], note["sender"], note["text"]), (frames.NOTICE, "cron", "好了"))
        card_ = frames.of("❓ 要哪個", by_bot=True, entry={"intent": "card", "framed": []})
        self.assertEqual(card_["kind"], frames.BOT)


if __name__ == "__main__":
    unittest.main()
