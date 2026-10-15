"""📋 on a message that ends cut short (stage 3b step 2.3b). A turn's thread shows the beginning of each long piece;
a piece that was cut now ends its message (one cut a message at most, at its end), and a 📋 on that message posts
the next 10000 characters of it into the same thread, cut short in its turn while there is more."""
import asyncio
import importlib.machinery
import importlib.util
import json
import os
import re
import tempfile
import unittest
from unittest import mock

import bot  # noqa: F401  (first: it puts the engine's folder on the path; the connectors lie beside dcbot/)
from altention.connectors.claude_code import transcript
from altention.tests.connectors.claude_code.test_hooks import line
from bot import process, remote
from tests import tmuxguard  # noqa: F401,E402  (no test reaches a real tmux server)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROSE = "\n".join("第 %d 行：這是一段說明文字，說到一半要換行。" % i for i in range(1200))       # about 30000 characters
FENCED = "先說一句。\n```python\n" + "\n".join("print(%d)  # 第 %d 行程式" % (i, i) for i in range(900)) + "\n```\n說完了。"
OUTPUT = "".join("line %d of the output ``` with a fence in it\n" % i for i in range(700))             # about 30000
COMMAND = "echo " + " ".join("字%d" % i for i in range(3000))
TASK = "找出 " + "、".join("第 %d 個檔案" % i for i in range(300))
INPUT = {"script": "s" * 2500, "note": "`ticks` in it"}


class Message(object):
    def __init__(self, mid, content):
        self.id, self.content = mid, content


class Thread(object):
    def __init__(self, tid, parent_id):
        self.id, self.parent_id, self.name, self.sent, self.notes, self.next_id = tid, parent_id, "", [], [], tid * 1000

    async def send(self, text, **kw):
        if kw.get("delete_after"):
            self.notes.append(text)
            return None
        self.next_id += 1
        self.sent.append(Message(self.next_id, text))
        self.sent[-1].reference = kw.get("reference")
        self.sent[-1].pings = kw.get("mention_author")
        return self.sent[-1]

    async def fetch_message(self, mid):
        return next(m for m in self.sent if m.id == mid)


class Channel(object):
    id = 5

    def __init__(self, bot_):
        self.sent, self.bot = [], bot_

    async def send(self, text, **kw):
        self.sent.append(text)

    async def fetch_message(self, mid):
        channel = self

        class Reply(object):
            async def create_thread(self, name, **kw):
                thread = channel.bot.threads[900] = Thread(900, channel.id)
                thread.name = name
                return thread
        return Reply()


class Bot(object):
    def __init__(self, st):
        self.channels_state, self.threads, self.saved = {"5": st}, {}, 0
        self.ch = Channel(self)

    def get_channel(self, cid):
        return self.ch if cid == 5 else self.threads.get(cid)

    async def fetch_channel(self, cid):
        raise LookupError("no such channel")

    def save(self):
        self.saved += 1
        json.dumps(self.channels_state)  # (it is written to the state file: nothing in it that JSON cannot hold)


def marks(text):
    return len(re.findall(r"…（還有 \d+ 字，對這則按 📋 接著看）", text))


class World(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, "s.jsonl")
        with open(self.path, "w", encoding="utf-8") as f:
            f.write(line("user", PROSE, promptId="p1")
                    + line("assistant", [{"type": "text", "text": FENCED}])
                    + line("assistant", [{"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": COMMAND, "description": "Say a lot"}}])
                    + line("user", [{"type": "tool_result", "tool_use_id": "t1", "content": OUTPUT}])
                    + line("assistant", [{"type": "tool_use", "id": "t2", "name": "Agent", "input": {"prompt": TASK}}])
                    + line("assistant", [{"type": "tool_use", "id": "t3", "name": "Workflow", "input": INPUT}])
                    + line("assistant", [{"type": "tool_use", "id": "t4", "name": "Read", "input": {"file_path": "/w/a"}}])
                    + line("assistant", [{"type": "text", "text": "短短的一句。"}]))
        self.st = {"turns": {"7": [self.path, 0, None, None]}, "turn_ids": {"7": "p1"}}
        self.bot = Bot(self.st)

    def st_(self):
        return self.bot.channels_state["5"]

    def react(self, channel_id, message_id):
        asyncio.run(process.show(self.bot, mock.Mock(channel_id=channel_id, message_id=message_id, guild_id=77)))

    def open_thread(self):
        self.react(5, 7)
        return self.bot.threads[900]

    def cut_messages(self, thread):
        return [m for m in thread.sent if str(m.id) in self.st_().get("more", {})]


class TheThread(World):
    def test_a_message_has_one_cut_at_most_and_at_its_end(self):
        thread = self.open_thread()
        whole = "\n".join(m.content for m in thread.sent)
        self.assertEqual(marks(whole), 6, "you, said, the command, the output, the task, the other tool's input")
        for m in thread.sent:
            self.assertLessEqual(len(m.content), 1900)
            self.assertLessEqual(marks(m.content), 1, m.content[-200:])
            if marks(m.content):
                self.assertTrue(process.MARK.search(m.content), "at its very end: " + m.content[-80:])
        cut = self.cut_messages(thread)
        self.assertEqual(len(cut), 6, "each is remembered by its own message")
        self.assertEqual([self.st_()["more"][str(m.id)][:3] for m in cut],
                         [["7", 0, "text"], ["7", 1, "text"], ["7", 2, "command"], ["7", 3, "text"], ["7", 4, "prompt"], ["7", 5, "input"]])
        self.assertIn("🔧 **Read** `/w/a`", whole)
        self.assertTrue(thread.sent[-1].content.endswith("💬 短短的一句。"), "what was not cut is put together as before")
        self.assertEqual(self.bot.saved, 2, "the thread's id, and the cuts")

    def test_prose_is_cut_at_the_end_of_a_line_and_an_open_fence_is_closed(self):
        thread = self.open_thread()
        you, said = self.cut_messages(thread)[:2]
        body = you.content.split("\n…（還有")[0]
        self.assertTrue(body.endswith("換行。"), body[-40:])
        self.assertEqual(said.content.count("```") % 2, 0, "the fence the cut left open is closed in the message")
        at = self.st_()["more"][str(said.id)][3]
        self.assertEqual(FENCED[at], "\n", "the rest begins at a line's end")
        self.assertEqual(transcript.fence_at(FENCED, at), "python")

    def test_what_fits_is_not_cut_and_nothing_is_remembered(self):
        with open(self.path, "w", encoding="utf-8") as f:
            f.write(line("user", "hi", promptId="p1") + line("assistant", [{"type": "text", "text": "hello"}]))
        thread = self.open_thread()
        self.assertEqual(([m.content for m in thread.sent], self.st_().get("more")), (["🧑 **你**\nhi\n💬 hello"], None))
        self.assertEqual(self.bot.saved, 1)


class Pressed(World):
    def press_through(self, thread, message):
        """📋 on a cut message, then on the last of what that posted, until nothing is cut: what was posted."""
        posted, rounds = [], 0
        while True:
            before = len(thread.sent)
            self.react(900, message.id)
            new = thread.sent[before:]
            self.assertTrue(new, thread.notes)
            posted.append(new)
            rounds += 1
            if not marks(new[-1].content):
                return posted, rounds
            self.assertEqual(sum(marks(m.content) for m in new), 1)
            message = new[-1]
            self.assertLess(rounds, 20)

    def test_the_output_ten_thousand_characters_at_a_time_until_it_is_all_there(self):
        thread = self.open_thread()
        output = self.cut_messages(thread)[3]
        posted, rounds = self.press_through(thread, output)
        whole = OUTPUT.strip().replace("```", "'''")
        at = self.st_()["more"][str(output.id)][3]
        self.assertIn(rounds, (-(-(len(whole) - at) // process.EXPAND), -(-(len(whole) - at) // process.EXPAND) + 1), "about 10000 characters a press")
        for new in posted:
            self.assertTrue(new[0].content.startswith("-# ⤵ 接「🔧 Bash 的輸出」第 "), new[0].content[:60])
            for m in new:
                self.assertLessEqual(len(m.content), 2000)
                body = "\n".join(ln for ln in process.MARK.sub("", m.content).rstrip("\n").split("\n") if not ln.startswith(("-# ⤵", "-# ⤴")))
                self.assertTrue(body.startswith("```\n") and body.endswith("\n```"), "each message a fence of its own: " + body[:20])
                self.assertTrue(body[4:-4].split("\n")[0].startswith("line ") and body[4:-4].endswith("a fence in it"), "whole lines: " + body[4:44])
        text = "\n".join(t for new in posted for t in self.texts(new))
        self.assertEqual(text, whole[at:].strip("\n"), "what was posted, put together line by line, is the rest of the output")
        self.assertFalse(marks(posted[-1][-1].content), "the last has no cut")
        self.assertTrue(posted[-1][-1].content.endswith("\n-# ⤴ 「🔧 Bash 的輸出」到這裡結束"), "it says where the lot ends")
        self.assertEqual(sum(m.content.count("到這裡結束") for new in posted for m in new), 1)
        self.assertEqual(thread.notes, [])
        pressed = output
        for new in posted:  # the first of each batch is a reply to the very message that was pressed; the others are not
            self.assertEqual((new[0].reference.message_id, new[0].reference.channel_id, new[0].pings), (pressed.id, 900, False))
            self.assertEqual([m.reference for m in new[1:]], [None] * (len(new) - 1))
            pressed = new[-1]

    def test_a_stretch_is_ten_thousand_characters_of_the_text_not_counting_the_fences(self):
        thread = self.open_thread()
        output = self.cut_messages(thread)[3]
        at = self.st_()["more"][str(output.id)][3]
        self.assertTrue(600 < at <= 1200 and OUTPUT.strip()[at] == "\n", "what the thread shows first ends at a line's end too")
        self.react(900, output.id)
        rec = self.st_()["more"]
        first_new = rec[str(output.id)][4]
        last = [m for m in thread.sent if str(m.id) in rec and rec[str(m.id)][:3] == ["7", 3, "text"] and m.id != output.id]
        self.assertEqual(len(last), 1)
        nxt = rec[str(last[0].id)][3]
        self.assertTrue(at + process.EXPAND // 2 < nxt <= at + process.EXPAND and OUTPUT.strip()[nxt] == "\n", "ten thousand of the text at most, to a line's end")
        self.assertIn(first_new, [m.id for m in thread.sent], "and the message pressed remembers the first of what it posted")
        head = next(m for m in thread.sent if m.id == first_new).content.split("\n")[0]
        self.assertEqual(head, "-# ⤵ 接「🔧 Bash 的輸出」第 %s–%s 字（共 %s 字）" % (format(at + 1, ","), format(nxt, ","), format(len(OUTPUT.strip()), ",")))

    def test_prose_goes_on_at_line_ends_with_its_fence_opened_again(self):
        thread = self.open_thread()
        said = self.cut_messages(thread)[1]
        posted, rounds = self.press_through(thread, said)
        for new in posted:
            for m in new:
                self.assertEqual(m.content.count("```") % 2, 0, "fences balanced in every message: " + m.content[:30])
        self.assertTrue(posted[0][0].content.startswith("-# ⤵ 接「💬 中途的話」第 "), posted[0][0].content[:50])
        self.assertEqual(posted[0][0].content.split("\n")[1], "```python", "the fence that was open where it goes on")
        self.assertTrue(posted[-1][-1].content.endswith("-# ⤴ 「💬 中途的話」到這裡結束"), "a thing said before the turn's last: words on the way")
        lines = [ln for new in posted for m in new for ln in process.MARK.sub("", m.content).split("\n") if ln and not ln.startswith(("```", "-# ⤵", "-# ⤴"))]
        at = self.st_()["more"][str(said.id)][3]
        self.assertEqual(lines, [ln for ln in FENCED[at:].split("\n") if ln and not ln.startswith("```")], "every line of the rest, once, in order")

    def test_the_command_the_task_and_the_other_tools_input_too(self):
        thread = self.open_thread()
        cut = self.cut_messages(thread)
        for message, whole, shown, name in ((cut[2], COMMAND, 800, "🔧 Bash 的指令"), (cut[4], TASK, 200, "🔧 Agent 的任務"),
                                            (cut[5], json.dumps(INPUT, ensure_ascii=False), 300, "🔧 Workflow 的輸入")):
            at = self.st_()["more"][str(message.id)][3]
            self.assertLessEqual(at, shown)
            posted, rounds = self.press_through(thread, message)
            self.assertTrue(posted[0][0].content.startswith("-# ⤵ 接「%s」第 " % name), posted[0][0].content[:50])
            self.assertTrue(posted[-1][-1].content.endswith("-# ⤴ 「%s」到這裡結束" % name))
            text = "".join(t for new in posted for t in self.texts(new))
            self.assertEqual(text.replace("\n", ""), whole[at:].replace("```", "'''").replace("\n", ""))

    def test_pressed_again_it_points_to_what_was_posted(self):
        thread = self.open_thread()
        output = self.cut_messages(thread)[3]
        self.react(900, output.id)
        count = len(thread.sent)
        self.react(900, output.id)
        self.assertEqual(len(thread.sent), count, "nothing is posted twice")
        first = self.st_()["more"][str(output.id)][4]
        self.assertEqual(thread.notes, ["📋 這則已經展開過，接下去的在下面：https://discord.com/channels/77/900/%d" % first])

    def test_pressed_twice_at_once_it_is_posted_once(self):
        """The reaction taken off and put on again while the stretch is still being posted (#code-review)."""
        thread = self.open_thread()
        output = self.cut_messages(thread)[3]
        count, real = len(thread.sent), thread.send
        gate = []

        async def slow(text, **kw):
            if not kw.get("delete_after") and not gate:
                gate.append(asyncio.Event())
                await gate[0].wait()
            return await real(text, **kw)

        async def go():
            payload = mock.Mock(channel_id=900, message_id=output.id, guild_id=77)
            with mock.patch.object(thread, "send", slow):
                first = asyncio.ensure_future(process.show(self.bot, payload))
                await asyncio.sleep(0)
                await asyncio.sleep(0)
                await process.show(self.bot, payload)  # pressed again meanwhile
                midway = (len(thread.sent), list(thread.notes))
                gate[0].set()
                await first
            return midway
        midway = asyncio.run(go())
        self.assertEqual(midway, (count, ["📋 這則正在展開，稍等一下。"]))
        once = len(thread.sent) - count
        self.assertGreater(once, 0)
        self.react(900, output.id)
        self.assertEqual(len(thread.sent) - count, once, "and afterwards it points to what was posted")
        self.assertEqual(process.EXPANDING, set())

    def texts(self, messages, prose=False):
        """The text itself of posted stretch messages (the fences put round it and the MORE line taken off)."""
        out = []
        for m in messages:
            body = process.MARK.sub("", m.content).rstrip("\n")
            body = "\n".join(ln for ln in body.split("\n") if not ln.startswith(("-# ⤵", "-# ⤴")))  # (the head and foot lines of a batch)
            out.append("\n".join(ln for ln in body.split("\n") if not ln.startswith("```")) if prose else body[4:-4])
        return out

    def fail_third(self, thread, message):
        count, real, calls = len(thread.sent), thread.send, []

        async def failing(text, **kw):
            calls.append(1)
            if len(calls) == 3:
                raise OSError("Discord went away")
            return await real(text, **kw)
        with mock.patch.object(thread, "send", failing):
            with self.assertRaises(OSError):
                asyncio.run(process.show(self.bot, mock.Mock(channel_id=900, message_id=message.id, guild_id=77)))
        return count

    def test_posting_that_fails_halfway_is_gone_on_with_from_where_it_stopped(self):
        """The third message of a stretch fails (Discord). Pressed again, the same message goes on from the third:
        nothing twice, nothing out of reach; when all of it is there it points to the first of it."""
        thread = self.open_thread()
        output = self.cut_messages(thread)[3]
        count = self.fail_third(thread, output)
        self.assertEqual(len(thread.sent) - count, 2, "two got through")
        rec = self.st_()["more"][str(output.id)]
        self.assertEqual((rec[4], rec[5]), (None, thread.sent[count].id), "not done; the first of what is there is remembered")
        whole = OUTPUT.strip().replace("```", "'''")
        shown_to = self.st_()["more"][str(output.id)][3]
        two = "\n".join(self.texts(thread.sent[count:]))
        self.assertTrue(whole[rec[3]] == "\n" and whole[:rec[3]].endswith(two), "up to where the text is there: the end of the second message's last line")
        self.assertEqual(len([m for m in thread.sent[count:] if str(m.id) in self.st_()["more"]]), 0)
        self.assertEqual(process.EXPANDING, set())
        self.react(900, output.id)  # pressed again
        after = thread.sent[count:]
        self.assertGreater(len(after), 2)
        self.assertEqual(thread.notes, [], "it went on: no word of 'already'")
        got = "\n".join(self.texts(after))
        begins = whole.index(got)
        self.assertTrue(got and begins <= 1201, "put together line by line: the output from where the thread's message was cut, nothing twice, nothing missing")
        self.assertEqual(len([m for m in after if m.content.startswith("-# ⤵")]), 2, "each press says which stretch it is")
        rec = self.st_()["more"][str(output.id)]
        self.assertEqual((rec[4], len(rec)), (thread.sent[count].id, 5), "done now, and it points to the first of all that was posted")
        self.assertEqual(marks(after[-1].content), 1, "the last carries the next cut as ever")
        last = self.st_()["more"][str(after[-1].id)]
        self.assertEqual(whole[begins:last[3]].strip("\n"), got)
        self.react(900, output.id)
        self.assertIn("這則已經展開過", thread.notes[-1])
        posted, rounds = self.press_through(thread, after[-1])
        rest = "\n".join(t for new in posted for t in self.texts(new))
        self.assertEqual(got + "\n" + rest, whole[begins:], "and pressed on to the end: the whole output, once")

    def test_prose_that_fails_halfway_too(self):
        thread = self.open_thread()
        said = self.cut_messages(thread)[1]
        at = self.st_()["more"][str(said.id)][3]
        count = self.fail_third(thread, said)
        self.react(900, said.id)
        posted, rounds = self.press_through(thread, thread.sent[-1]) if marks(thread.sent[-1].content) else ([], 0)
        lines = [ln for t in self.texts(thread.sent[count:], prose=True) for ln in t.split("\n") if ln]
        self.assertEqual(lines, [ln for ln in FENCED[at:].split("\n") if ln and not ln.startswith("```")], "every line of the rest once, in order")
        for m in thread.sent[count:]:
            self.assertEqual(m.content.count("```") % 2, 0)
            self.assertLessEqual(len(m.content), 1950)

    def test_the_very_first_message_failing_leaves_the_record_as_it_was(self):
        thread = self.open_thread()
        output = self.cut_messages(thread)[3]
        before = list(self.st_()["more"][str(output.id)])

        async def failing(text, **kw):
            raise OSError("Discord went away")
        with mock.patch.object(thread, "send", failing):
            with self.assertRaises(OSError):
                asyncio.run(process.show(self.bot, mock.Mock(channel_id=900, message_id=output.id, guild_id=77)))
        self.assertEqual(self.st_()["more"][str(output.id)], before)

    def test_a_message_that_was_never_cut_and_one_no_longer_remembered(self):
        thread = self.open_thread()
        self.react(900, thread.sent[-1].id)
        self.assertEqual(thread.notes, ["📋 這則沒有被省略的內容。"])
        output = self.cut_messages(thread)[3]
        self.st_()["more"].pop(str(output.id))
        self.react(900, output.id)
        self.assertEqual(thread.notes[-1], "📋 這則的紀錄已經不在了。", "it ends with the cut's own words: it was cut, the record is gone")
        self.assertEqual(self.bot.ch.sent, [], "nothing is said in the channel")

    def test_after_a_restart_it_goes_on(self):
        thread = self.open_thread()
        output = self.cut_messages(thread)[3]
        again = Bot(json.loads(json.dumps(self.st_())))
        again.threads[900] = thread
        self.bot = again
        before = len(thread.sent)
        self.react(900, output.id)
        self.assertGreater(len(thread.sent), before)

    def test_the_turn_gone_or_no_longer_remembered(self):
        thread = self.open_thread()
        output = self.cut_messages(thread)[3]
        os.remove(self.path)
        self.react(900, output.id)
        self.assertEqual(thread.notes, ["📋 這一輪的對話紀錄已經不在了。"])
        self.assertIsNone(self.st_()["more"][str(output.id)][4], "not marked as posted")

    def test_only_the_newest_are_remembered_and_they_go_with_their_turn(self):
        st = {"turns": {"7": ["/p", 0, None, None]}}
        for i in range(process.MORE_KEEP + 5):
            process.note_more(st, 1000 + i, 7, (3, "text", 1200, 9000))
        self.assertEqual((len(st["more"]), "1004" in st["more"], "1005" in st["more"]), (process.MORE_KEEP, False, True))
        self.assertEqual(st["more"]["1005"], ["7", 3, "text", 1200, None])
        process.remember(st, [8], "/p", 10, None)
        self.assertEqual(len(st["more"]), process.MORE_KEEP, "its turn is still remembered")
        st["turns"].pop("7")
        process.remember(st, [9], "/p", 20, None)
        self.assertEqual(st["more"], {}, "the turn is forgotten: so are its cut messages")

    def test_a_reply_in_the_channel_is_shown_as_ever(self):
        self.react(5, 8)
        self.assertEqual(self.bot.ch.sent, ["📋 這則沒有記下過程（不是 Claude 的回覆，或是這個功能上線前的回覆）。"])
        self.open_thread()
        self.react(5, 7)
        self.assertEqual(self.bot.ch.sent[-1], "📋 這則的過程在 <#900>。")


class FencesPaired(unittest.TestCase):
    """Every message of a turn's thread has its code fences paired (#code-review-2's rehearsal of step 2.3b: a failed
    result began "❌ ```", a fence inside a line; cut there, one message ended with a fence too many and the next,
    the turn's reply, stood inside a block that was never closed)."""

    @staticmethod
    def messages(items):
        return [text for text, _ in process.messages_of(process.pieces(items))]

    def check(self, messages, why):
        for m in messages:
            self.assertEqual(m.count("```") % 2, 0, (why, m[:60], m[-60:]))
            self.assertLessEqual(len(m), 2000, why)

    def test_a_failed_result_near_where_a_message_is_cut(self):
        """The rehearsal's turn: a Grep with a long pattern whose call fails ("No such tool available"), then the
        reply. Every length of the pattern, so the cut falls at every place around the failed result."""
        for n in range(1500, 1960, 3):
            items = [("asked", "go"), ("said", "I will look."), ("tool", "Grep", {"pattern": "p" * n, "path": "/w"}),
                     ("result", {"content": "Error: No such tool available: Grep", "is_error": True}), ("said", "ANS-more.")]
            messages = self.messages(items)
            self.check(messages, n)
            self.assertTrue(messages[-1].endswith("💬 ANS-more."), n)
            last = messages[-1]
            self.assertFalse(last.startswith("```") and last.count("```") == 1, "the reply does not stand inside a block left open")
            self.assertEqual(process.pieces(items)[3], "❌\n```\nError: No such tool available: Grep\n```", "the failed result, its mark on a line of its own")

    def test_text_that_holds_fences_of_its_own(self):
        odd_said = "看這個：\n```\n沒有關的區塊"
        inline = "用 ```code``` 寫在行內，" * 150 + "還有一個 ``` 沒有關"
        for items in ([("asked", "go"), ("said", odd_said), ("said", "下一段不在區塊裡")],
                      [("asked", inline), ("said", "ok")],
                      [("asked", "go"), ("tool", "Agent", {"prompt": "任務：```\n" + "x" * 500}), ("said", "done")],
                      [("asked", "go"), ("tool", "Agent", {"description": "看 ``` 這個"}), ("tool", "WebSearch", {"query": "``` in a query"}), ("said", "done")],
                      [("asked", "go"), ("said", "a ``` b " * 700), ("result", {"content": "out", "is_error": True}), ("said", "end")]):
            messages = self.messages(items)
            self.check(messages, items[1][:2])
        self.assertEqual(self.messages([("said", odd_said), ("said", "下一段不在區塊裡")]), ["💬 看這個：\n```\n沒有關的區塊\n```\n💬 下一段不在區塊裡"])

    def test_a_cut_piece_keeps_the_cut_line_last(self):
        piece = process.pieces([("tool", "Agent", {"prompt": "任務：```\n" + "x" * 500})])[0]
        self.assertTrue(process.MARK.search(piece), piece[-60:])
        self.assertEqual(piece.count("```") % 2, 0)
        self.assertEqual(piece.more[:3], (0, "prompt", 200))
        self.assertTrue(piece.endswith("\n```\n" + process.MORE % (piece.more[3] - 200)), "the fence is closed ahead of the line that says it is cut")

    def test_paired_and_whole_fences(self):
        self.assertEqual(process.paired("a ``` b"), "a ``` b\n```")
        self.assertEqual(process.paired("a ```b``` c"), "a ```b``` c")
        self.assertEqual(process.whole_fences(["x ```py", "still code", "end ``` done", "plain"]),
                         ["x ```py\n```", "```\nstill code\n```", "```\nend ``` done", "plain"])
        self.assertEqual(process.whole_fences(["all", "plain"]), ["all", "plain"])

    def test_a_result_shown_does_not_end_in_an_empty_line(self):
        piece = process.pieces([("result", {"content": "1\n2\n" + "3\n" * 2000})])[0]
        self.assertNotIn("\n\n```", piece)


class RoomForTheFrame(unittest.TestCase):
    """No message of a stretch is over Discord's 2000 characters, whatever the tool is called and however much is
    left (#code-review of 0f35ba5: the lines that begin and end a batch bear the tool's name; a long name with
    about 1840 characters left made a message of 2002, which Discord refuses: nothing came, at every press)."""

    def batches(self, tool, shown, total, newlines=False):
        text = ("".join("%07d\n" % i for i in range(total // 8 + 1)) if newlines else "x" * total)[:total]
        items = [("tool", tool, {"q": 1}), ("result", {"content": text})]
        out, at = [], shown
        while at < len(text.strip()):
            got = transcript.piece(items, 1, transcript.TEXT, at, process.EXPAND)
            left = got["total"] - got["next"]
            top, end = process.head(got, at, transcript.TEXT), process.MORE % left if left > 0 else process.foot(got, transcript.TEXT)
            parts = process.stretch(got, first=len(top) + 1, last=len(end) + 1)
            parts[-1] = (parts[-1][0] + "\n" + end, parts[-1][1])
            parts[0] = (top + "\n" + parts[0][0], parts[0][1])
            self.assertEqual(process.fitting(parts), parts, "the room was reckoned right: the last guard changes nothing")
            self.assertEqual(parts[-1][1], got["next"])
            out.append(parts)
            at = got["next"]
        return out, text.strip()

    def test_the_longest_names_and_every_length_left(self):
        longest = 0
        for tool in ("Bash", "mcp__claude_ai_Google_Drive__complete_authentication", "m" * 64, "工" * 64):
            for newlines in (False, True):
                for left in [1800, 1830, 1850, 1851] + list(range(1700, 1960, 7)) + [10000, 11840, 23000]:
                    batches, whole = self.batches(tool, 1200, 1200 + left, newlines)
                    for parts in batches:
                        for body, _ in parts:
                            longest = max(longest, len(body))
                            self.assertLessEqual(len(body), 2000, (tool[:20], left, newlines))
                            self.assertEqual(body.count("```") % 2, 0)
                    got = "".join(b.split("```\n", 1)[1].rsplit("\n```", 1)[0] for parts in batches for b, _ in parts)
                    self.assertEqual(got.replace("\n", ""), whole[1200:].replace("\n", ""), (tool[:20], left, newlines))
                    self.assertEqual(sum(b.count("到這裡結束") for parts in batches for b, _ in parts), 1)
        self.assertGreater(longest, 1800, "and the room is used")

    def test_a_long_name_is_shortened_in_the_small_lines(self):
        got = {"text": "x", "total": 9, "next": 9, "of": {"kind": "result", "tool": "m" * 64}}
        self.assertEqual(process.named(got), "🔧 %s… 的輸出" % ("m" * (process.NAME_MAX - 1)))
        self.assertEqual(process.named({"of": {"kind": "result", "tool": "t" * process.NAME_MAX}}), "🔧 %s 的輸出" % ("t" * process.NAME_MAX))
        self.assertEqual(process.named({"of": {"kind": "result", "tool": "Bash"}}), "🔧 Bash 的輸出")


class TheLastGuard(World):
    def test_a_message_too_long_all_the_same_is_posted_without_its_small_lines_or_cut_again(self):
        """Should the room be reckoned wrong one day: nothing is refused whole. The text gets there, the cut line
        stays last, and it is said once in the log."""
        head, code = "-# ⤵ 接「🔧 X 的輸出」第 1–9 字（共 9 字）", "```\n" + "y" * 1985 + "\n```"
        with mock.patch("builtins.print") as printed, mock.patch.object(process, "fitted_said", False):
            got = process.fitting([(head + "\n" + code, 50), ("short", 60)])
            self.assertEqual(got, [(code, 50), ("short", 60)], "over 2000 with the small line, not without")
            long = "```\n" + "\n".join("z" * 99 for _ in range(30)) + "\n```\n" + process.MORE % 7
            got = process.fitting([(head + "\n" + long, 70)])
            self.assertEqual(([at for _, at in got], len(got)), ([None, 70], 2), "cut again: its place stays with the last part")
            for body, _ in got:
                self.assertLessEqual(len(body), process.DISCORD_MAX)
                self.assertEqual(body.count("```") % 2, 0)
            self.assertTrue(process.MARK.search(got[-1][0]))
            self.assertEqual("".join(b for b, _ in got).count("z"), 2970)
            self.assertEqual(printed.call_count, 1, "said once")
            odd = process.fitting([("w" * 5000 + "\n" + process.MORE % 3 + "\n" + "v" * 100, 80)])  # (nothing it is handed makes it fail)
            self.assertTrue(all(len(b) <= process.DISCORD_MAX for b, _ in odd) and odd[-1][1] == 80)

    def test_posted_through_the_guard_and_gone_on_with(self):
        thread = self.open_thread()
        output = self.cut_messages(thread)[3]
        real = process.stretch

        def too_long(got, first=0, last=0):
            parts = real(got, first, last)
            return [(parts[0][0][:-4] + "\n" + "q" * 300 + "\n```", parts[0][1])] + parts[1:]  # (the first reckoned 300 too long)
        with mock.patch.object(process, "stretch", too_long), mock.patch("builtins.print"), mock.patch.object(process, "fitted_said", False):
            before = len(thread.sent)
            self.react(900, output.id)
        new = thread.sent[before:]
        self.assertTrue(new and all(len(m.content) <= process.DISCORD_MAX for m in new), [len(m.content) for m in new])
        self.assertEqual(thread.notes, [])
        self.assertEqual(self.st_()["more"][str(output.id)][4], new[0].id, "it was posted, all of it")


class OnAnotherMachine(World):
    """The same thread and the same stretches when the transcript is on another machine: tools/dcremote's own turn()
    and part() are what answer, through JSON as over SSH."""

    def setUp(self):
        super().setUp()
        loader = importlib.machinery.SourceFileLoader("dcremote_more", os.path.join(ROOT, "tools", "dcremote"))
        self.tool = importlib.util.module_from_spec(importlib.util.spec_from_loader("dcremote_more", loader))
        loader.exec_module(self.tool)
        self.calls, self.answer = [], None

    def remote_world(self):
        here = World("setUp")
        here.setUp()
        self.addCleanup(here.tmp.cleanup)
        return here

    def over_there(self):
        import contextlib
        import io

        async def dcremote(host, *args, **kw):
            self.calls.append((host,) + args)
            if self.answer is not None:
                return self.answer
            out = io.StringIO()
            with mock.patch.object(self.tool.cc_transcript, "own", lambda path, config: path), contextlib.redirect_stdout(out):
                getattr(self.tool, args[0])(*args[1:])
            return 0, out.getvalue()
        return mock.patch.object(remote, "dcremote", dcremote)

    def test_the_same_as_here_stretch_for_stretch(self):
        local = self.remote_world()
        thread_here = local.open_thread()
        self.st_()["turn_hosts"] = {"7": "lap"}
        with self.over_there():
            thread = self.open_thread()
            self.assertEqual([m.content for m in thread.sent], [m.content for m in thread_here.sent], "the thread itself")
            for n in (1, 3, 2):
                there, here = self.cut_messages(thread)[n], local.cut_messages(thread_here)[n]
                a, b = len(thread.sent), len(thread_here.sent)
                self.react(900, there.id)
                local.react(900, here.id)
                self.assertEqual([m.content for m in thread.sent[a:]], [m.content for m in thread_here.sent[b:]], n)
        self.assertEqual([c[:2] for c in self.calls], [("lap", "turn"), ("lap", "part"), ("lap", "part"), ("lap", "part")])
        self.assertEqual(self.calls[1][2:], (self.path, "0", "p1", "1", "text", str(self.st_()["more"][str(self.cut_messages(thread)[1].id)][3]), "10000"))

    def test_when_it_cannot_be_asked(self):
        self.st_()["turn_hosts"] = {"7": "lap"}
        with self.over_there():
            thread = self.open_thread()
            output = self.cut_messages(thread)[3]
            for answer, said in (((255, ""), "📋 這一輪在 lap 上，現在連不上，等它回來再按一次。"),
                                 ((1, ""), "📋 這一輪在 lap 上，那台的程式還沒更新到能讀它，稍後再按一次。"),
                                 ((4, ""), "📋 這一輪在 lap 上，那台回來的內容讀不出來。"), ((0, "Traceback"), "📋 這一輪在 lap 上，那台回來的內容讀不出來。"),
                                 ((0, '{"text": 5}'), "📋 這一輪在 lap 上，那台回來的內容讀不出來。"), ((0, "null\n"), "📋 這一輪的對話紀錄已經不在了。")):
                self.answer = answer
                before = len(thread.sent)
                self.react(900, output.id)
                self.assertEqual((thread.notes[-1], len(thread.sent)), (said, before), answer)
                self.assertIsNone(self.st_()["more"][str(output.id)][4], "the record stays: pressed again later")
            self.answer = None
            self.react(900, output.id)
            self.assertGreater(len(thread.sent), before, "and then it goes on")


if __name__ == "__main__":
    unittest.main()
