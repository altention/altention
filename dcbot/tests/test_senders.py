"""C-172a P1d (manager 11-17): who sent a tool's message, as the ledger keeps it: a fixed key (bot/senders.py), the
words written before turned into keys once (bot/fromkeys.py), and the words shown only when read."""
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
from unittest import mock

from bot import fromkeys, frames, ledger, posts, rules, senders
from tests import tmuxguard  # noqa: F401  (no test reaches a real tmux server)


class Keys(unittest.TestCase):
    def test_made_parsed_and_said(self):
        self.assertEqual(senders.key(channel=5), "channel:5")
        self.assertEqual(senders.key(machine="lap", folder="/w/proj/", claude=True), "claude:lap:proj")
        self.assertEqual(senders.key(machine="lap", folder="proj"), "program:lap:proj")
        self.assertEqual(senders.key(), "program:?:?")
        self.assertEqual(senders.parse("claude:lap:a:b"), ("claude", "lap", "a:b"))
        for not_one in (None, "", "channel:x", "#bot-dev", "頻道 5 的 Claude（自稱）", "program:lap", "user:5"):
            self.assertIsNone(senders.parse(not_one), not_one)
        self.assertEqual(senders.label("channel:5"), "<#5>")
        self.assertEqual(senders.label("channel:5", lambda cid: {5: "lab"}.get(cid)), "#lab")
        self.assertEqual(senders.label("channel:6", lambda cid: None), "<#6>")
        self.assertEqual(senders.label("claude:lap:proj"), "lap 上的 Claude（proj）")
        self.assertEqual(senders.label("program:lap:proj"), "lap 上的程式（proj）")
        self.assertEqual(senders.label("#old words"), "#old words")
        self.assertEqual(senders.who("channel:5"), "<#5> 的 Claude")
        self.assertEqual((senders.who("claude:lap:p"), senders.who("x")), ("lap 上的 Claude（p）", "x"))
        self.assertIsNone(senders.label(None))

    def test_the_words_written_before(self):
        ids = {"lab": [10], "twin": [11, 12]}.get
        for said, want in (("頻道 5 的 Claude（自稱）", "channel:5"), ("#lab", "channel:10"),
                           ("#twin", None), ("#renamed-since", None),
                           ("NV 上的程式（experiments）", "program:NV:experiments"),
                           ("lap 上的 Claude（proj）", "claude:lap:proj"),
                           ("CGI 上的程式（自稱）", None), ("CGI 的 tmux 窗格 %3（自稱）", None),
                           ("channel:5", None), (None, None), ("機器人", None)):
            self.assertEqual(senders.old_key(said, lambda n: ids(n, ())), want, said)
        listed = {"nv-host": "NV"}.get  # (a hostname altention dc post wrote, by the list's name: manager 11-17)
        self.assertEqual(senders.old_key("nv-host 上的程式（q）", lambda n: (), listed), "program:NV:q")
        self.assertEqual(senders.old_key("mac-x 上的 Claude（p）", lambda n: (), listed), "claude:mac-x:p", "not listed: kept")


class Turned(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        p = mock.patch.dict(os.environ, {"DCBOT_LEDGER": os.path.join(self.tmp, "ledger.json")})
        p.start()
        self.addCleanup(p.stop)
        ledger.reset()
        self.addCleanup(ledger.reset)
        for mid, frm in ((1, "頻道 5 的 Claude（自稱）"), (2, "#lab"), (3, "#twin"), (4, "CGI 的 tmux 窗格 %3（自稱）"),
                         (5, "NV 上的程式（q）"), (6, None), (7, "channel:9")):
            ledger.note(10, mid, "post" if frm else "reply", frm=frm)
        self.audits = []

    def bot(self, names=(("lab", 10), ("twin", 11), ("twin", 12)), guild=True):
        async def audit(text):
            self.audits.append(text)
        chans = [types.SimpleNamespace(name=n, id=i) for n, i in names]
        return types.SimpleNamespace(get_guild=lambda gid: types.SimpleNamespace(text_channels=chans) if guild else None,
                                     audit=audit)

    def froms(self):
        return [ledger.entry(m, 10)["from"] for m in range(1, 8)]

    def test_once_written_marked_and_said(self):
        asyncio.run(fromkeys.once(self.bot(), 1))
        want = ["channel:5", "channel:10", "#twin", "CGI 的 tmux 窗格 %3（自稱）", "program:NV:q", None, "channel:9"]
        self.assertEqual(self.froms(), want)
        ledger.reset()
        self.assertEqual(self.froms(), want, "on disk")
        self.assertTrue(fromkeys.done())
        self.assertEqual(self.audits, ["[ledger] 寄件人改成固定的鍵：轉了 3 則，留原樣 2 則（同名的頻道、找不到的名字、tmux 窗格號不轉；"
                                       "留原樣的不能再用 dcctl edit 改）"])
        asyncio.run(fromkeys.once(self.bot(), 1))
        self.assertEqual(len(self.audits), 1, "once")

    def test_a_hostname_by_the_lists_name(self):
        from altention import config as altconfig
        with mock.patch.object(altconfig, "name_of", lambda h: {"NV": None, "nv-host": "NV"}.get(h)):
            ledger.note(10, 8, "post", frm="nv-host 上的 Claude（w）")
            asyncio.run(fromkeys.once(self.bot(), 1))
        self.assertEqual(ledger.entry(8, 10)["from"], "claude:NV:w")

    def test_not_written_no_mark_and_again_next_time(self):
        with mock.patch.object(ledger, "save", lambda: False):
            asyncio.run(fromkeys.once(self.bot(), 1))
        self.assertFalse(fromkeys.done())
        self.assertEqual(self.audits, [])
        asyncio.run(fromkeys.once(self.bot(), 1))
        self.assertTrue(fromkeys.done())
        self.assertIn("轉了 0 則，留原樣 2 則", self.audits[0], "turned in memory the first time: never twice")

    def test_no_server_yet_nothing(self):
        asyncio.run(fromkeys.once(self.bot(guild=False), 1))
        self.assertFalse(fromkeys.done())
        self.assertEqual(self.froms()[0], "頻道 5 的 Claude（自稱）")


class Shown(unittest.TestCase):
    def test_a_dc_posts_sender_by_its_channel_else_machine_and_folder(self):
        self.assertEqual(posts.sender_key({}, types.SimpleNamespace(id=7, name="x")), "channel:7")
        self.assertEqual(posts.sender_key({"verified": True, "from_machine": "lap", "from_folder": "proj",
                                           "from_host": "lap-host", "from_name": "lap-host 上的 Claude（other）"}, None),
                         "claude:lap:proj")
        self.assertEqual(posts.sender_key({"from_host": "nv", "from_name": "nv 上的程式（queue）"}, None),
                         "program:nv:queue", "an older altention dc post: the folder in its words")

    def test_read_back_and_refused_in_words(self):
        what = frames.of("hi", by_bot=True, entry={"intent": "post", "from": "channel:5"},
                         name_of=lambda cid: "lab" if cid == 5 else None)
        self.assertEqual(what["sender"], "#lab")
        self.assertIn("這則是 lap 上的程式（q） 發的", rules.change_refused({"intent": "post", "from": "program:lap:q"},
                                                                     "channel:5"))


if __name__ == "__main__":
    unittest.main()
