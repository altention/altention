"""C-202 point 3 (manager 11-18): a post another channel's Claude sent that was typed but not entered, or not handed
over for good, is said in the channel it came from and told to the Claude there; one held up for now is kept and
handed again (C-74), as before. Telling that Claude never sends anything back: no two channels telling each other."""
import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))))
from altention import testing as _testing  # noqa: E402
_testing.guard_home()  # tests never touch the real home (altention/testing.py)
import asyncio
import unittest
from unittest import mock

import bot  # noqa: F401  (first: it puts the engine's folder on the path)
from bot import messages, posts, tmux_bridge
from tests import tmuxguard  # noqa: F401  (no test reaches a real tmux server)
from tests.test_safety import run
from tests.fakepane import Screens, box
from tests.test_turn_end import Ch, Host


class Sender(Ch):
    id, name = 9, "st-1"


class Two(Host):
    def __init__(self, ch, sender):
        Host.__init__(self, ch)
        self.sender, self.told = sender, []
        host = self

        class P(object):
            async def tell(self, ch, text):
                host.told.append((ch.id, text))

            async def answered(self, *a, **k):
                pass
        self.posts = P()

    def get_channel(self, cid):
        return {self.ch.id: self.ch, self.sender.id: self.sender}.get(cid)


class Tell(unittest.TestCase):
    def setUp(self):
        self.ch, self.sender = Ch(), Sender()
        self.host = Two(self.ch, self.sender)
        self.bridge = tmux_bridge.TmuxBridge(self.host)

    def send(self, message, r=None, pressed=None, ensured=True):
        async def nothing(*a, **k):
            return None

        async def checked(*a, **k):
            return r if r is not None else tmux_bridge.Typing()

        async def go():
            got = await self.bridge.submit(message, "（這是 #st-1 傳到這個頻道的訊息）\nhi")
            await asyncio.sleep(0.05)  # (the Claude there is told in a task of its own)
            return got
        return run(go(), Screens([box("")]), mock.patch.object(self.bridge, "ensure", nothing) if ensured else mock.patch.object(self, "_x", None, create=True),
                   mock.patch.object(self.bridge, "checked", checked), mock.patch.object(self.bridge, "box_unknown", nothing),
                   mock.patch.object(self.bridge, "note_paste", nothing),
                   mock.patch.object(self.bridge, "enter", mock.AsyncMock(return_value=pressed)),
                   mock.patch.object(tmux_bridge.tmuxio, "type_text", nothing),
                   mock.patch.object(tmux_bridge, "SUBMIT_DELAY", 0), mock.patch.object(tmux_bridge, "RECEIPT_SECS", 0.01),
                   mock.patch.object(tmux_bridge.posts_mod, "listens", lambda st: True))

    def post(self):
        return posts.Origin(self.ch, auto=True, sender=9)

    def refused(self, code, **data):
        r = tmux_bridge.Typing()
        r.refuse(code, **data)
        return r

    def test_typed_not_entered_is_said_where_it_came_from_and_to_its_claude(self):
        self.assertIs(self.send(self.post()), False)  # (not entered: submit() answers told()'s False)
        words = messages.note("post.not_entered", to="c")
        self.assertEqual([m.content for m in self.sender.sent], [words])
        self.assertTrue(self.sender.sent[0].silent)
        self.assertEqual(self.host.told, [(9, "（機器人通知）" + words)])
        self.assertNotIn("對話框", words, "manager 11-18: no inner words")
        self.assertIn("要的話晚點再送一次（先用 `altention dc read c` 看它有沒有收到，免得重複）", words)
        self.assertEqual(len(self.ch.sent), 1, "said in its own channel as before")

    def test_one_not_handed_over_for_good_says_why(self):
        self.send(self.post(), self.refused("claude_code.no_colour", text="abc"))
        said, = [m.content for m in self.sender.sent]
        self.assertEqual(said, "⚠️ 你傳給 #c 的訊息沒有送到它的 Claude：終端機的輸入框裡已經有字（abc）。"
                               "這個原因不會自己消失，現在重送也一樣，要等那邊處理好再送。", "#discord-lab 11-18")
        self.assertNotIn("**", said)
        self.assertEqual(len(self.host.told), 1)

    def test_one_held_up_for_now_is_kept_as_before_and_not_said_there(self):
        self.send(self.post(), self.refused("claude_code.dialog"))
        self.assertEqual((self.sender.sent, self.host.told), ([], []))

    def test_what_is_told_to_a_claude_is_never_sent_back(self):
        """Posts.tell() types it as posts.Origin(ch): no sender. Not entered there, it is said there only."""
        self.assertIs(self.send(posts.Origin(self.ch)), False)
        self.assertIn("沒有按 Enter", self.ch.sent[-1].content)
        self.assertEqual((self.sender.sent, self.host.told), ([], []))

    def not_opened(self, r=None):
        async def fails(*a, **k):
            raise tmux_bridge.NotOpened(tmux_bridge.NotOpened.PANE_GONE, "窗格裡已經沒有在跑的 Claude")

        async def nothing(*a, **k):
            return None
        patches = [mock.patch.object(self.bridge, "start_failed", nothing)]
        if r is not None:
            patches.append(mock.patch.object(tmux_bridge, "not_opened", lambda rt, e, message=True: r))
        with mock.patch.object(self.bridge, "ensure", fails):
            for p in patches:
                p.start()
            try:
                self.send(self.post(), ensured=False)
            finally:
                for p in patches:
                    p.stop()

    def test_not_opened_for_now_is_not_said_there(self):
        """ensure() failing: every reason it has now passes (temporary: kept and handed again, C-74)."""
        self.not_opened()
        self.assertEqual((self.sender.sent, self.host.told), ([], []))

    def test_not_opened_for_good_is_said_there(self):
        """#code-review on b8fbb3fd (low): the call after ensure() had no test (no reason of its is permanent yet)."""
        self.not_opened(self.refused("claude_code.no_colour", text="abc"))
        self.assertEqual(len(self.sender.sent), 1)
        self.assertIn("這個原因不會自己消失", self.sender.sent[0].content)
        self.assertEqual(len(self.host.told), 1)
        self.assertEqual(self.bridge.tell_tasks, set(), "the task let go once done")

    def test_entered_says_nothing_more(self):
        self.send(self.post(), pressed="pressed")
        self.assertEqual((self.sender.sent, self.host.told), ([], []))

    def test_a_sender_channel_gone_is_said_in_audit(self):
        self.host.sender = type("Gone", (), {"id": -1})()
        self.send(self.post())
        self.assertEqual(self.host.audits, [messages.note("post.not_entered", to="c")])

    def test_the_gist_of_a_notice(self):
        self.assertEqual(tmux_bridge.gist("🚫 `/exit` 不能從 Discord 送：會結束對話。真的要用，請到終端機自己打。"),
                         "/exit 不能從 Discord 送：會結束對話。")
        self.assertTrue(len(tmux_bridge.gist("⚠️ " + "字" * 300)) <= 160)
        self.assertEqual(tmux_bridge.gist("⚠️ 輸入框裡有字，所以沒有送出。再試。"), "輸入框裡有字。")
        self.assertEqual(tmux_bridge.gist("沒有標點的一句"), "沒有標點的一句。")


if __name__ == "__main__":
    unittest.main()
