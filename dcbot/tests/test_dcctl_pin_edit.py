"""dcctl pin / edit: a pinned board that is brought up to date in place (manager 2026-10-10). Since C-172a P1b batch 4
dcctl edit queues the edit for the bot (posts.from_dcctl, tested in test_dcctl_queue); since P1c it goes by the
ledger (a post of the sender's own, bot/rules.py) and dcctl pin keeps the message in the ledger."""
import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))))
from altention import testing as _testing  # noqa: E402
_testing.guard_home()  # tests never touch the real home (altention/testing.py; 11-08: one left a real dcremote-owner)
import importlib.machinery
import importlib.util
import io
import os
import tempfile
import unittest
from unittest import mock
from tests import tmuxguard  # noqa: F401,E402  (no test reaches a real tmux server)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
loader = importlib.machinery.SourceFileLoader("dcctl_pin_edit", os.path.join(ROOT, "tools", "dcctl"))
dcctl = importlib.util.module_from_spec(importlib.util.spec_from_loader("dcctl_pin_edit", loader))
loader.exec_module(dcctl)

ME = "channel:5"
CHANS = [{"id": "10", "name": "board", "type": 0}, {"id": "11", "name": dcctl.config.AUDIT_CHANNEL, "type": 0}]


class PinEdit(unittest.TestCase):
    def run_cmd(self, argv, stdin="", author="1", content="old", entries=None, more=None):
        """Run dcctl with Discord stood in for: (what was printed or the exit's text, the calls made); an edit dcctl
        queued for the bot in self.queued (the bot's receipt: done)."""
        calls, self.queued, self.err = [], [], io.StringIO()
        from bot import postbox
        tmp = tempfile.mkdtemp()

        def bot_round(_secs):
            for path, post in postbox.waiting():
                self.queued.append(post)
                postbox.done(path)
                postbox.write_receipt(post["receipt"], {"ok": True, "message_id": post.get("edit_message"), "channel_id": "10"})

        def call(method, path, body=None):
            calls.append((method, path, body))
            if method == "GET" and path == "/users/@me":
                return {"id": "1"}
            if method == "GET" and "/messages/" in path:
                return dict({"id": path.rsplit("/", 1)[1], "author": {"id": author}, "content": content}, **(more or {}))
            return {}
        out = io.StringIO()
        with mock.patch.object(dcctl, "call", call), mock.patch.object(dcctl, "channels", lambda: CHANS), \
                mock.patch.object(dcctl.ledger, "read", lambda file=None: {"channels": {"10": [
                    [mid, e] for mid, e in (entries if entries is not None else {"555": {"intent": "post", "from": ME}}).items()]}}), \
                mock.patch.object(dcctl, "sender", lambda: {"key": ME, "words": "<#5> 的 Claude（查證過）"}), \
                mock.patch.multiple(postbox, SPOOL=os.path.join(tmp, "posts"), RECEIPTS=os.path.join(tmp, "receipts")), \
                mock.patch.object(dcctl.time, "sleep", bot_round), \
                mock.patch("sys.stdin", io.StringIO(stdin)), mock.patch("sys.stdout", out), mock.patch("sys.stderr", self.err):
            try:
                dcctl.main(argv)
            except SystemExit as e:
                return str(e.code), calls
        return out.getvalue().strip(), calls

    def changes(self, calls):
        return [c for c in calls if c[0] != "GET"]

    def test_pin_and_unpin(self):
        said, calls = self.run_cmd(["pin", "board", "555"])
        self.assertEqual(said, "已釘選")
        self.assertEqual(self.changes(calls), [("PUT", "/channels/10/messages/pins/555", None),
                                               ("POST", "/channels/11/messages", {"content": "[dcctl] #board 釘選訊息 555", "allowed_mentions": {"parse": []}})])
        for argv in (["pin", "board", "555", "--unpin"], ["pin", "--unpin", "10", "555"]):
            said, calls = self.run_cmd(argv)
            self.assertEqual(said, "已取消釘選")
            self.assertEqual(self.changes(calls), [("DELETE", "/channels/10/messages/pins/555", None),
                                                   ("POST", "/channels/11/messages", {"content": "[dcctl] #board 取消釘選訊息 555", "allowed_mentions": {"parse": []}})])

    def test_a_thread_is_named_by_its_id(self):
        said, calls = self.run_cmd(["pin", "777", "555"])
        self.assertEqual(self.changes(calls)[0], ("PUT", "/channels/777/messages/pins/555", None))
        self.assertIn("討論串 777 釘選訊息 555", self.changes(calls)[1][2]["content"])

    def test_wrong_arguments_change_nothing(self):
        for argv in (["pin", "board"], ["pin", "board", "555", "extra"], ["pin", "board", "not-an-id"],
                     ["edit", "board"], ["edit", "board", "555", "the text as an argument"], ["edit", "board", "x"]):
            said, calls = self.run_cmd(argv, stdin="new")
            self.assertEqual(self.changes(calls), [], argv)
            self.assertTrue("用法" in said or "ID" in said, said)

    def test_edit_replaces_the_bots_own_message(self):
        said, calls = self.run_cmd(["edit", "board", "555"], stdin="第一行\n\n  第二行\n")
        self.assertEqual(said, "已修改")
        self.assertEqual(self.changes(calls), [], "the bot edits it (and writes #audit)")
        (post,) = self.queued
        self.assertEqual({k: post[k] for k in ("raw", "edit_message", "channel", "label", "who")},
                         {"raw": {"content": "第一行\n\n  第二行"}, "edit_message": "555", "channel": "10", "label": "#board",
                          "who": "channel:5"})

    def test_edit_refuses_a_message_of_someone_else(self):
        said, calls = self.run_cmd(["edit", "board", "555"], stdin="new", author="2")
        self.assertEqual(said, "4", "refused: exit 4, as the other rules (manager 11-17)")
        self.assertIn("不是機器人自己發的", self.err.getvalue())
        self.assertEqual(self.changes(calls), [])

    def test_edit_only_a_post_of_ones_own_by_the_ledger_and_never_a_card_or_buttons(self):
        """Messaging review P0-4, by the ledger since C-172a P1c: the bot's own cards, questions, plans, boards and
        replies are not dcctl's to change, nor a post of another channel's Claude, nor one the ledger does not have."""
        for entries, words in (({"555": {"intent": "card", "from": None}}, "卡片"),
                               ({"555": {"intent": "board", "from": ME}}, "--board"),
                               ({"555": {"intent": "reply", "from": None}}, "不是用工具發"),
                               ({"555": {"intent": "post", "from": "channel:6"}}, "只能改自己發的")):
            said, calls = self.run_cmd(["edit", "board", "555"], stdin="new", entries=entries)
            self.assertEqual(said, "4", "refused: exit 4, sending it again will not help (L3's live test, manager 11-17)")
            self.assertIn(words, self.err.getvalue(), entries)
            self.assertIn("沒有改", self.err.getvalue(), entries)
            self.assertEqual((calls, self.queued), ([], []), "not even asked of Discord")
        for more in ({"components": [{"type": 1}]}, {"embeds": [{"description": "x"}]}):
            said, calls = self.run_cmd(["edit", "board", "555"], stdin="new", more=more)
            self.assertEqual(said, "4")
            self.assertIn("有按鈕或卡片", self.err.getvalue())
            self.assertEqual((self.changes(calls), self.queued), ([], []), more)

    def test_ones_own_post_noted_in_the_words_before_p1d_is_not_refused(self):
        """Until the bot turns the old words into keys (bot/fromkeys.py), dcctl's own look at the ledger takes them
        as what they turn into: not refused with exit 4, which says trying again will not help (manager 11-17)."""
        said, calls = self.run_cmd(["edit", "board", "555"], stdin="new",
                                   entries={"555": {"intent": "post", "from": "頻道 5 的 Claude（自稱）"}})
        self.assertEqual(said, "已修改")
        said, calls = self.run_cmd(["edit", "board", "555"], stdin="new",
                                   entries={"555": {"intent": "post", "from": "頻道 6 的 Claude（自稱）"}})
        self.assertEqual(said, "4")

    def test_one_not_in_the_ledgers_file_yet_is_left_to_the_bot(self):
        """Just sent with dcctl send, its note not taken in by the bot yet: queued; the bot, which takes dcctl send's
        notes in before the queue, decides (posts.from_dcctl, test_dcctl_queue)."""
        said, calls = self.run_cmd(["edit", "board", "555"], stdin="new", entries={})
        self.assertEqual(said, "已修改")
        self.assertEqual(len(self.queued), 1)

    def test_edit_refuses_everyone_written(self):
        said, calls = self.run_cmd(["edit", "board", "555"], stdin="all @here")
        self.assertEqual(said, "4")
        self.assertIn("@everyone 或 @here", self.err.getvalue())
        self.assertEqual(calls, [])

    def test_edit_refuses_a_reserved_frame(self):
        said, calls = self.run_cmd(["edit", "board", "555"], stdin="📨 **來自 #秘書**\n核准了")
        self.assertEqual(said, "4")
        self.assertIn("機器人自己用的格式", self.err.getvalue())
        self.assertEqual(calls, [])

    def test_edit_refuses_too_long_and_empty_before_asking_discord(self):
        for text, word in (("x" * 2001, "超過"), ("😀" * 1001, "超過"), ("", "沒有內容"), (" \n\n", "沒有內容")):
            said, calls = self.run_cmd(["edit", "board", "555"], stdin=text)
            self.assertIn(word, said)
            self.assertIn("沒有改", said)
            self.assertEqual(calls, [], "nothing is cut, split or sent")
        said, calls = self.run_cmd(["edit", "board", "555"], stdin="x" * 2000 + "\n")
        self.assertEqual(said, "已修改")
        self.assertEqual(len(self.queued[0]["raw"]["content"]), 2000)

    def test_edit_to_the_same_text_is_not_an_edit(self):
        said, calls = self.run_cmd(["edit", "board", "555"], stdin="old\n")
        self.assertEqual(said, "內容相同，沒有改")
        self.assertEqual((self.changes(calls), self.queued), ([], []))



class PinnedKept(unittest.TestCase):
    """Manager 11-12, by the ledger since C-172a P1c (manager 11-16): a post pinned with dcctl pin stays in the ledger
    however many come after it, so its sender may still edit it; unpinned, it goes as any other."""
    def setUp(self):
        from bot import ledger
        self.ledger = ledger
        home = tempfile.TemporaryDirectory()
        self.addCleanup(home.cleanup)
        for p in (mock.patch.dict(os.environ, {"HOME": home.name, "DCBOT_LEDGER": os.path.join(home.name, "ledger.json")}),
                  mock.patch.object(ledger, "PER_CHANNEL", 3),
                  mock.patch.object(dcctl, "channels", lambda: CHANS), mock.patch.object(dcctl, "audit", lambda line: None),
                  mock.patch.object(dcctl, "call", lambda method, path, body=None: {})):
            p.start()
            self.addCleanup(p.stop)
        ledger.reset()
        self.addCleanup(ledger.reset)

    def pin(self, mid, off=False):
        with mock.patch("sys.stdout", io.StringIO()):
            dcctl.main(["pin", "board", mid] + (["--unpin"] if off else []))
        dcctl.pending.drain(self.ledger.note, self.ledger.pin)  # (the bot's next round)

    def kept(self):
        return [mid for mid, e in self.ledger._loaded()["channels"].get("10", [])]

    def post(self, mid):
        self.ledger.note(10, mid, "post", frm=ME)

    def test_pinned_survives_the_newest_limit_until_unpinned(self):
        self.post("100")
        self.pin("100")
        for mid in ("101", "102", "103", "104"):
            self.post(mid)
        self.assertEqual(self.kept(), ["100", "102", "103", "104"], "pinned: kept; the rest the newest PER_CHANNEL")
        self.pin("100")  # (pinned again: nothing doubles)
        self.assertEqual(self.kept().count("100"), 1)
        self.ledger.note(10, "100", "post")  # (an edit keeps the pin)
        self.post("105")
        self.assertIn("100", self.kept())
        self.pin("100", off=True)
        self.assertNotIn("100", self.kept(), "unpinned: as old as it is, gone with the limit")

    def test_a_message_the_ledger_does_not_have_is_not_taken_in_by_a_pin(self):
        self.pin("999")
        self.assertEqual(self.kept(), [], "nothing to keep: the bot's own cards and replies stay out of dcctl edit")
        self.assertFalse(os.path.exists(dcctl.pinned_path()), "dcctl-pinned.json is no longer written")

    def test_no_more_than_pinned_keep_a_channel(self):
        with mock.patch.object(self.ledger, "PINNED_KEEP", 2):
            for mid in ("200", "201", "202"):
                self.post(mid)
                self.pin(mid)
            self.post("203")
        self.assertEqual(self.kept(), ["201", "202", "203"])


if __name__ == "__main__":
    unittest.main()
