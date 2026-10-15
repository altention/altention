"""dcctl pin / edit: a pinned board that is brought up to date in place (manager 2026-10-10)."""
import importlib.machinery
import importlib.util
import io
import os
import unittest
from unittest import mock
from tests import tmuxguard  # noqa: F401,E402  (no test reaches a real tmux server)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
loader = importlib.machinery.SourceFileLoader("dcctl_pin_edit", os.path.join(ROOT, "tools", "dcctl"))
dcctl = importlib.util.module_from_spec(importlib.util.spec_from_loader("dcctl_pin_edit", loader))
loader.exec_module(dcctl)

CHANS = [{"id": "10", "name": "board", "type": 0}, {"id": "11", "name": dcctl.config.AUDIT_CHANNEL, "type": 0}]


class PinEdit(unittest.TestCase):
    def run_cmd(self, argv, stdin="", author="1", content="old"):
        """Run dcctl with Discord stood in for: (what was printed or the exit's text, the calls made)."""
        calls = []

        def call(method, path, body=None):
            calls.append((method, path, body))
            if method == "GET" and path == "/users/@me":
                return {"id": "1"}
            if method == "GET" and "/messages/" in path:
                return {"id": path.rsplit("/", 1)[1], "author": {"id": author}, "content": content}
            return {}
        out = io.StringIO()
        with mock.patch.object(dcctl, "call", call), mock.patch.object(dcctl, "channels", lambda: CHANS), \
                mock.patch("sys.stdin", io.StringIO(stdin)), mock.patch("sys.stdout", out):
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
                                               ("POST", "/channels/11/messages", {"content": "[dcctl] #board 釘選訊息 555"})])
        for argv in (["pin", "board", "555", "--unpin"], ["pin", "--unpin", "10", "555"]):
            said, calls = self.run_cmd(argv)
            self.assertEqual(said, "已取消釘選")
            self.assertEqual(self.changes(calls), [("DELETE", "/channels/10/messages/pins/555", None),
                                                   ("POST", "/channels/11/messages", {"content": "[dcctl] #board 取消釘選訊息 555"})])

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
        self.assertEqual(self.changes(calls), [("PATCH", "/channels/10/messages/555", {"content": "第一行\n\n  第二行"}),
                                               ("POST", "/channels/11/messages", {"content": "[dcctl] #board 修改訊息 555（10 字）"})])

    def test_edit_refuses_a_message_of_someone_else(self):
        said, calls = self.run_cmd(["edit", "board", "555"], stdin="new", author="2")
        self.assertIn("不是機器人自己發的", said)
        self.assertEqual(self.changes(calls), [])

    def test_edit_refuses_too_long_and_empty_before_asking_discord(self):
        for text, word in (("x" * 2001, "超過"), ("😀" * 1001, "超過"), ("", "沒有內容"), (" \n\n", "沒有內容")):
            said, calls = self.run_cmd(["edit", "board", "555"], stdin=text)
            self.assertIn(word, said)
            self.assertIn("沒有改", said)
            self.assertEqual(calls, [], "nothing is cut, split or sent")
        said, calls = self.run_cmd(["edit", "board", "555"], stdin="x" * 2000 + "\n")
        self.assertEqual(said, "已修改")
        self.assertEqual(len(self.changes(calls)[0][2]["content"]), 2000)

    def test_edit_to_the_same_text_is_not_an_edit(self):
        said, calls = self.run_cmd(["edit", "board", "555"], stdin="old\n")
        self.assertEqual(said, "內容相同，沒有改")
        self.assertEqual(self.changes(calls), [])


if __name__ == "__main__":
    unittest.main()
