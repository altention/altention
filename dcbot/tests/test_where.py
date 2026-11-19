"""C-201 first batch (design instance docs/refactor/c201-design.md section 2, manager 11-17): one record per channel
of where its conversation is (bot/where.py), written from the fields the bot goes by whenever the state is saved, and
/where's card."""
import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))))
from altention import testing as _testing  # noqa: E402
_testing.guard_home()  # tests never touch the real home (altention/testing.py)
import asyncio
import os
import unittest
from unittest import mock

from bot import messages, where
from tests import tmuxguard  # noqa: F401  (no test reaches a real tmux server)

BOT = "/tmp/tmux-7/dcbot"


def rec(st, name="lab"):
    return where.record(st, name, "lap", BOT)


class Put(unittest.TestCase):
    """C-201 2a: where.put() is the one writer of a channel's place: the old fields as given (an older bot reads them
    after a rollback) and its `where` made from them at once."""
    def setUp(self):
        self.addCleanup(setattr, where, "context", where.context)
        where.context = lambda: ("cgi", "/bot.sock")

    def test_fields_and_record_written_together(self):
        st = {"cwd": "/w", "session_id": "S"}
        rec = where.put(st, "exp-a", pane={"socket": "/s", "id": "%3", "pid": 9}, pane_session="work")
        self.assertEqual(st["pane"], {"socket": "/s", "id": "%3", "pid": 9})
        self.assertEqual(st["pane_session"], "work")
        self.assertIs(rec, st["where"])
        self.assertEqual((rec["place"]["pane"], rec["place"]["session"], rec["names"]["channel"], rec["owned"]),
                         ("%3", "work", "exp-a", "user"))
        where.put(st, pane=None, pane_session=None, tmux="dc-exp-a-0001")
        self.assertNotIn("pane", st)
        self.assertEqual((st["where"]["place"]["session"], st["where"]["place"]["socket"], st["where"]["names"]["channel"]),
                         ("dc-exp-a-0001", "/bot.sock", "exp-a"), "the name it had kept")

    def test_only_place_fields(self):
        with self.assertRaises(KeyError):
            where.put({}, cwd="/x")

    def test_no_context_leaves_the_record(self):
        st = {"cwd": "/w", "session_id": "S", "where": {"place": {"session": "old"}}}
        where.context = None
        where.put(st, tmux="newer")
        self.assertEqual((st["tmux"], st["where"]["place"]["session"]), ("newer", "old"))

    def test_another_machines_state_loses_its_place_keeps_what_was_seen(self):
        """#code-review on 1a4cad50: the standby's copy for the primary kept its old place, and the primary's next
        save said each channel failback moved was written past put()."""
        st = {"cwd": "/w", "session_id": "S", "where": {"place": {"session": "old"}, "seen": {"state": "here"}}}
        where.put(st, remake=False, tmux="new")
        self.assertEqual((st["tmux"], st["where"]), ("new", {"seen": {"state": "here"}}))
        said = []
        where.stamp({"1": st}, lambda cid: "a", "cgi", "/bot.sock", say=said.append)
        self.assertEqual(said, [])
        self.assertEqual(st["where"]["place"]["session"], "new")
        self.assertEqual(st["where"]["seen"], {"state": "here"})

    def test_stamp_says_a_place_written_past_put(self):
        st = {"cwd": "/w", "session_id": "S"}
        where.put(st, "a", tmux="dc-a")
        said = []
        where.stamp({"1": st}, lambda cid: "a", "cgi", "/bot.sock", say=said.append)
        self.assertEqual(said, [])
        st["tmux"] = "dc-b"  # (past put())
        where.stamp({"1": st}, lambda cid: "a", "cgi", "/bot.sock", say=said.append)
        self.assertEqual(len(said), 1)
        self.assertIn("written past where.put()", said[0])
        self.assertEqual(st["where"]["place"]["session"], "dc-b", "the fields win")

    def test_nothing_else_in_bot_writes_the_place_fields(self):
        """A writer past put() would leave `where` behind till the next save (and stamp() would say so)."""
        import re
        root = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bot")
        keys = "|".join(where.PLACE_FIELDS)
        pattern = re.compile(r'\b(st|state|sst)(\[["\'](%s)["\']\]\s*=[^=]|\.pop\(["\'](%s)["\']|\.setdefault\(["\'](%s)["\']'
                             r'|\.update\(\{["\'](%s)["\'])|\[["\'](pane|home)["\']\]\[[^]]+\]\s*=[^=]' % (keys, keys, keys, keys))
        found = []
        for name in sorted(os.listdir(root)):
            if name.endswith(".py") and name != "where.py":
                with open(os.path.join(root, name), encoding="utf-8") as f:
                    found += ["%s:%d: %s" % (name, n, line.strip()) for n, line in enumerate(f, 1) if pattern.search(line)]
        self.assertEqual(found, [])


class Record(unittest.TestCase):
    def test_a_session_on_the_bots_server(self):
        r = rec({"tmux": "dc-lab-0042", "session_id": "3f2a0000-1111", "cwd": "/w/proj"})
        self.assertEqual(r["place"], {"machine": "lap", "socket": BOT, "session": "dc-lab-0042", "window": None,
                                      "pane": None, "pid": None})
        self.assertEqual((r["owned"], r["follow"], r["folder"]), ("bot", ["channel", "conversation", "window", "session"],
                                                                  "/w/proj"))
        self.assertEqual(r["agent"], {"kind": "claude", "conversation": "3f2a0000-1111",
                                      "transcript": "~/.claude/projects/-w-proj/3f2a0000-1111.jsonl"})
        self.assertEqual(r["names"], {"channel": "lab", "session": "dc-lab-0042", "window": None})

    def test_an_attached_pane_here_or_on_another_machine(self):
        r = rec({"pane": {"socket": "/tmp/tmux-7/default", "id": "%3", "pid": 77}, "pane_session": "st", "cwd": "/w"})
        self.assertEqual(r["place"], {"machine": "lap", "socket": "/tmp/tmux-7/default", "session": "st", "window": None,
                                      "pane": "%3", "pid": 77})
        self.assertEqual((r["owned"], r["follow"]), ("user", ["channel", "conversation", "window"]),
                         "the owner's session never follows a rename")
        r = rec({"pane": {"host": "nv", "socket": "/tmp/tmux-7/default", "id": "%9", "pid": 5}, "cwd": "/w"})
        self.assertEqual(r["place"]["machine"], "nv")

    def test_a_window_the_bot_opened_in_a_tmux(self):
        st = {"home": {"host": "nv", "socket": "/tmp/tmux-7/default", "session": "work", "window": "exp"}, "cwd": "/w"}
        self.assertEqual(rec(st)["place"], {"machine": "nv", "socket": "/tmp/tmux-7/default", "session": "work",
                                            "window": "exp", "pane": None, "pid": None})
        st["pane"] = {"socket": "/tmp/tmux-7/default", "id": "%4", "pid": 8}
        r = rec(st)
        self.assertEqual((r["place"]["pane"], r["place"]["window"], r["place"]["machine"], r["owned"]), ("%4", "exp", "nv", "bot"))

    def test_what_the_look_saw_of_the_same_pane_wins(self):
        st = {"pane": {"socket": "/tmp/tmux-7/default", "id": "%3", "pid": 77}, "pane_session": "old", "cwd": "/w",
              "where": {"seen": {"state": "here", "machine": "lap", "pane": "%3", "session": "renamed", "window": "w2", "at": 1}}}
        r = rec(st)
        self.assertEqual((r["place"]["session"], r["place"]["window"], r["names"]["session"]), ("renamed", "w2", "renamed"))
        self.assertEqual(r["seen"]["at"], 1, "kept")
        st["where"]["seen"]["pane"] = "%4"  # (another pane: the old fields)
        self.assertEqual(rec(st)["place"]["session"], "old")
        st["where"]["seen"].update(pane="%3", state="gone")
        self.assertEqual(rec(st)["place"]["session"], "old", "not there: nothing taken from it")

    def test_channels_with_no_conversation_have_none(self):
        for st in ({"plain": True, "cwd": "/w"}, {"kind": "terminal", "pane": {"id": "%1"}}, {"kind": "files", "cwd": "/w"},
                   {"archived": {"pane": {"id": "%1"}}, "cwd": "/w"}, {"cwd": "/w"}, None):
            self.assertIsNone(rec(st), st)
        self.assertEqual(rec({"session_id": "abc", "cwd": "/w"})["place"]["machine"], "lap", "a conversation not open yet")

    def test_stamped_on_every_channel_and_dropped_when_none(self):
        state = {"1": {"tmux": "dc-a-0001", "cwd": "/w"}, "2": {"plain": True, "where": {"old": 1}}, "3": "junk"}
        where.stamp(state, {"1": "a", "2": "b"}.get, "lap", BOT)
        self.assertEqual(state["1"]["where"]["names"]["channel"], "a")
        self.assertNotIn("where", state["2"])

    def test_the_transcript_as_the_connector_names_its_folder(self):
        from altention.connectors.claude_code import sessions
        folder = "/w/my.proj/實驗 a"
        self.assertEqual(os.path.basename(os.path.dirname(where.transcript(folder, "x"))),
                         os.path.basename(sessions.project_dir(folder)))


class Card(unittest.TestCase):
    def card(self, r, **kw):
        return where.card(r, "lab", "lap", BOT, say=messages.where, clock=lambda ts: "@%d" % ts, **kw)

    def test_a_bots_session(self):
        r = rec({"tmux": "dc-lab-0042", "session_id": "3f2a0000-1111", "cwd": os.path.expanduser("~") + "/work/exp"})
        self.assertEqual(self.card(r, title="實驗 A"), "\n".join([
            "📍 #lab 現在在哪裡",
            "- 機器：lap · tmux（機器人的） · session dc-lab-0042 · window 0",
            "- 對話：Claude Code · 3f2a0000…「實驗 A」",
            "- 資料夾：~/work/exp",
            "- 改名時跟著變：頻道、對話、window、session"]))

    def test_an_attached_pane_seen_gone_and_no_conversation(self):
        r = rec({"pane": {"host": "nv", "socket": "/tmp/tmux-7/default", "id": "%12", "pid": 5}, "pane_session": "st",
                 "cwd": "/home/x/w", "where": {"seen": {"state": "gone", "at": 1000, "pane": "%12", "machine": "nv"}}})
        text = self.card(r)
        self.assertIn("- 機器：nv · tmux（你的） · session st · 窗格 %12", text)
        self.assertIn("- 對話：還沒有", text)
        self.assertIn("- 資料夾：/home/x/w", text, "another machine's folder: as it is")
        self.assertIn("- ⚠️ 上次對帳：@1000 窗格不見了", text)

    def test_a_pane_seen_respawned_is_said_being_checked(self):
        """#code-review low on e30ad2b3 (manager 11-18): the look's "respawned" (C-198) has a line too."""
        r = rec({"pane": {"host": "nv", "socket": "/tmp/tmux-7/default", "id": "%12", "pid": 5}, "pane_session": "st",
                 "cwd": "/home/x/w", "where": {"seen": {"state": "respawned", "at": 1000, "pane": "%12", "machine": "nv"}}})
        self.assertIn("- 上次對帳：@1000 窗格重開過，正在確認是不是同一個", self.card(r))

    def test_an_adapter_by_its_name(self):
        """Drill of d0f26f25, A (manager 11-20): pi, Codex said as they are; no id is not one the next message opens."""
        pane = {"host": "nv", "socket": "/tmp/tmux-7/default", "id": "%12", "pid": 5}
        r = rec({"pane": dict(pane, agent="pi"), "pane_session": "st", "cwd": "/w"})
        self.assertEqual(r["agent"]["kind"], "pi")
        self.assertIn("- 對話：pi（轉接層沒有給對話 id）", self.card(r))
        self.assertNotIn("下一則訊息會開一個", self.card(r))
        self.assertIn("- 改名時跟著變：頻道、window", self.card(r), "no conversation name to follow")
        r = rec({"pane": dict(pane, agent="codex"), "pane_session": "st", "cwd": "/w", "session_id": "019a0000-2222"})
        self.assertIn("- 對話：codex · 019a0000…", self.card(r))
        self.assertEqual(rec({"pane": pane, "pane_session": "st", "cwd": "/w"})["agent"]["kind"], "claude")

    def test_none(self):
        self.assertEqual(self.card(None), "📍 #lab 沒有 Claude 對話（不是對話頻道，或已經封存）")


class Command(unittest.TestCase):
    """!where (and /where): the record as the fields are now; the conversation's name from its file here."""
    def test_said_from_a_fresh_record(self):
        from bot import main
        bot = mock.Mock()
        st = {"tmux": "dc-lab-0042", "session_id": "3f2a0000-1111", "cwd": "/w"}
        bot.channels_state = {"5": st}
        bot.get_channel = lambda cid: mock.Mock(name="ch", id=cid)
        bot.where_context.return_value = ("lap", BOT)
        bot.save = lambda: where.stamp(bot.channels_state, lambda cid: "lab", "lap", BOT)
        ch = mock.Mock(id=5)
        ch.name = "lab"
        with mock.patch("altention.connectors.claude_code.sessions.summarize", return_value={"title": "實驗 A"}):
            text = asyncio.run(main.Bot.where_card(bot, ch, st))
        self.assertIn("「實驗 A」", text)
        self.assertIn("session dc-lab-0042", text)


if __name__ == "__main__":
    unittest.main()
