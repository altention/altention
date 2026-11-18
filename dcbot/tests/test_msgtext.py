"""C-172a P1a: one message as text, the same for every reader (bot/msgtext.py; instance docs/refactor/c172a-design.md).
Payloads in Discord's REST shape, as #discord-lab's samples (g1–g4) and the API give them."""
import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))))
from altention import testing as _testing  # noqa: E402
_testing.guard_home()  # tests never touch the real home (altention/testing.py)
import importlib.machinery
import importlib.util
import os
import types
import unittest

from bot import msgtext
from tests import tmuxguard  # noqa: F401  (no test reaches a real tmux server)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

TODO = {"flags": 32768, "components": [{"type": 17, "accent_color": 5793266, "components": [
    {"type": 10, "content": "## 📋 今天 to do"},
    {"type": 9, "components": [{"type": 10, "content": "⬜ 傳聯絡"}],
     "accessory": {"type": 2, "style": 3, "label": "完成", "custom_id": "user:1"}},
    {"type": 14},
    {"type": 9, "components": [{"type": 10, "content": "看圖"}],
     "accessory": {"type": 11, "media": {"url": "attachment://a.png"}, "description": "表格"}},
    {"type": 1, "components": [{"type": 2, "label": "好", "custom_id": "user:y"}, {"type": 2, "label": "不要", "custom_id": "user:n"}]},
    {"type": 1, "components": [{"type": 3, "custom_id": "user:s", "placeholder": "挑一個", "options": []}]},
    {"type": 12, "items": [{"media": {"url": "attachment://x.png"}, "description": "圖一"}, {"media": {"url": "attachment://y.png"}}]},
    {"type": 13, "file": {"url": "attachment://report.pdf"}},
]}]}


class Text(unittest.TestCase):
    def test_a_v2_message_top_to_bottom(self):
        self.assertEqual(msgtext.text_of(TODO).split("\n"), [
            "▌〔藍〕## 📋 今天 to do",
            "▌⬜ 傳聯絡 〔按鈕：完成〕",
            "▌",
            "▌看圖 〔圖：表格〕",
            "▌〔按鈕：好｜不要〕",
            "▌〔選單：挑一個〕",
            "▌〔圖 2 張：圖一〕",
            "▌〔檔案：report.pdf〕"])

    def test_colours_named(self):
        for value, name in ((0xF1C40F, "黃"), (0x2ECC71, "綠"), (0xE74C3C, "紅"), (0x9B59B6, "紫"), (0xE67E22, "橘")):
            self.assertEqual(msgtext.colour_name(value), name)
        self.assertIsNone(msgtext.colour_name(None))
        self.assertIsNone(msgtext.colour_name(True))

    def test_content_embeds_and_the_rest(self):
        m = {"content": "看這個", "embeds": [{"author": {"name": "📨 來自 #st-1"}, "title": "標題", "description": "內文",
                                              "fields": [{"name": "狀態", "value": "好"}, {"name": "", "value": "只有值"}],
                                              "footer": {"text": "頁尾"}}],
             "sticker_items": [{"name": "讚"}],
             "poll": {"question": {"text": "去哪"}, "answers": [{"poll_media": {"text": "A"}}, {"poll_media": {"text": "B"}}]}}
        self.assertEqual(msgtext.text_of(m).split("\n"),
                         ["看這個", "📨 來自 #st-1", "標題", "內文", "狀態：好", "只有值", "頁尾", "〔貼圖：讚〕", "〔投票：去哪｜A｜B〕"])

    def test_a_reply_a_voice_message_a_forward(self):
        m = {"content": "對", "referenced_message": {"author": {"username": "alice"}, "content": "x" * 60}}
        self.assertEqual(msgtext.text_of(m), "↪ 回覆 alice：%s…\n對" % ("x" * 50))
        self.assertEqual(msgtext.text_of({"flags": 1 << 13, "attachments": [{"duration_secs": 4.7}]}), "〔語音 4 秒〕")
        fwd = {"message_snapshots": [{"message": {"content": "", "components": [{"type": 10, "content": "原文"}]}}]}
        self.assertEqual(msgtext.text_of(fwd), "[轉發] 原文")

    def test_nothing_to_read_and_shapes_not_known(self):
        self.assertEqual(msgtext.text_of({}), "")
        self.assertEqual(msgtext.text_of(None), "")
        self.assertEqual(msgtext.text_of({"components": [{"type": 99, "components": [{"type": 10, "content": "裡面"}]}, "junk"]}), "裡面")

    def test_from_discord_py(self):
        class Comp(object):
            def to_dict(self):
                return {"type": 10, "content": "框裡"}

        class Broken(object):
            def to_dict(self):
                raise ValueError("no")
        msg = types.SimpleNamespace(id=5, content="", author=types.SimpleNamespace(bot=True, name="bot", global_name=None),
                                    embeds=[], components=[Comp(), Broken(), {"type": 10, "content": "dict 的"}],
                                    attachments=[], stickers=[], flags=types.SimpleNamespace(value=32768), reference=None)
        self.assertEqual(msgtext.text_of(msgtext.of_discord(msg)), "框裡\ndict 的")


class History(unittest.TestCase):
    """dcctl history (altention dc read) reads through it: what was read before is still there, and more."""
    def test_format_message(self):
        loader = importlib.machinery.SourceFileLoader("dcctl_msgtext", os.path.join(ROOT, "tools", "dcctl"))
        d = importlib.util.module_from_spec(importlib.util.spec_from_loader("dcctl_msgtext", loader))
        loader.exec_module(d)
        m = {"id": "9", "timestamp": "2026-11-16T05:00:00+00:00", "author": {"bot": True}, "content": "",
             "embeds": [{"author": {"name": "📨 來自 #st-1"}, "description": "hi", "footer": {"text": "暫停中"}}],
             "attachments": [{"filename": "a.txt", "url": "https://x/a.txt"}],
             "message_snapshots": [{"message": {"content": "原文", "attachments": [{"filename": "b.png", "url": "https://x/b"}]}}]}
        out = d.format_message(m, chars=0)
        for want in ("📨 來自 #st-1", "hi", "暫停中", "[轉發] 原文", "[附件] a.txt https://x/a.txt", "[附件] b.png https://x/b"):
            self.assertIn(want, out)
        v2 = dict(m, embeds=[], message_snapshots=[], attachments=[], **TODO)
        self.assertIn("▌⬜ 傳聯絡 〔按鈕：完成〕", d.format_message(v2, chars=0), "a V2 message no longer reads as nothing")


if __name__ == "__main__":
    unittest.main()
