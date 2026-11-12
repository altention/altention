"""C-87: altention dc post / dc notify --wait N: exit 0 and the message's ID once the bot has put it on Discord (a
receipt the bot writes), 3 when it is still queued when the time is up (the bot not running), 4 when the bot says it
was not shown; dcctl send by an ID reads no channel list and prints the message's ID."""
import asyncio
import importlib.machinery
import importlib.util
import io
import json
import os
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

from bot import posts, postbox
from tests import tmuxguard  # noqa: F401,E402  (no test reaches a real tmux server)
from tests.test_posts import Bot, Chan

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load(name, path):
    loader = importlib.machinery.SourceFileLoader(name, path)
    mod = importlib.util.module_from_spec(importlib.util.spec_from_loader(name, loader))
    loader.exec_module(mod)
    return mod


class Base(unittest.TestCase):
    def setUp(self):
        self.d = load("dcctl_wait", os.path.join(ROOT, "tools", "dcctl"))
        state = tempfile.TemporaryDirectory()
        self.addCleanup(state.cleanup)
        self.state = state.name
        self.spool, self.receipts = os.path.join(self.state, "posts"), os.path.join(self.state, "receipts")
        self.calls = []

        self.bodies = []

        def call(method, path, body=None):
            self.calls.append((method, path))
            self.bodies.append(body)
            return {"id": "777"} if method == "POST" else []
        for p in (mock.patch.object(self.d.config, "STATE", self.state), mock.patch.object(postbox, "SPOOL", self.spool),
                  mock.patch.object(postbox, "RECEIPTS", self.receipts), mock.patch.object(self.d, "call", call),
                  mock.patch.object(self.d.config, "GUILD_ID", 42)):
            p.start()
            self.addCleanup(p.stop)

    def run_post(self, post):
        """(exit code, stdout, stderr) of dcctl post."""
        out, err, code = io.StringIO(), io.StringIO(), 0
        with mock.patch.object(sys, "stdin", io.StringIO(json.dumps(post))), mock.patch("sys.stdout", out), \
                mock.patch("sys.stderr", err):
            try:
                self.d.main(["post"])
            except SystemExit as e:
                code = e.code
        return code, out.getvalue(), err.getvalue()

    def heartbeat(self, ago):
        with open(os.path.join(self.state, "heartbeat"), "w") as f:
            f.write(str(int(time.time()) - ago))

    def bot(self, receipt, delay=0.3):
        """A bot that takes the post from the spool after `delay` and writes `receipt` for it."""
        def go():
            time.sleep(delay)
            (path, post), = postbox.waiting(self.spool)
            postbox.write_receipt(post["receipt"], receipt, self.receipts)
            postbox.done(path)
        t = threading.Thread(target=go)
        t.start()
        self.addCleanup(t.join)


class Wait(Base):
    def test_no_bot_running_times_out_with_3_and_the_post_stays_queued(self):
        began = time.time()
        code, out, err = self.run_post({"channel": "2", "text": "x", "wait": 0.6})
        self.assertEqual(code, self.d.QUEUED)
        self.assertGreaterEqual(time.time() - began, 0.6)
        self.assertIn("機器人沒在跑（沒有心跳）", err)
        self.assertIn("還在佇列裡", err)
        self.assertNotIn("已送出", out, "nothing said to have gone")
        self.assertEqual(len(postbox.waiting(self.spool)), 1)
        self.heartbeat(300)
        code, out, err = self.run_post({"channel": "2", "text": "x", "wait": 0.3})
        self.assertEqual(code, 3)
        self.assertRegex(err, r"30\d 秒沒有心跳", "a second or two pass while it waits (#code-review of 9129636)")
        self.heartbeat(1)
        code, out, err = self.run_post({"channel": "2", "text": "x", "wait": 0.3})
        self.assertIn("還沒處理到它", err)

    def test_shown_gives_0_and_the_message_id_on_the_last_line(self):
        self.heartbeat(1)
        self.bot({"ok": True, "message_id": "123456", "channel_id": "2"})
        code, out, err = self.run_post({"channel": "2", "text": "x", "wait": 10})
        self.assertIn(code, (0, None))
        self.assertEqual(out.splitlines()[-1], "123456")
        self.assertEqual(os.listdir(self.receipts), [], "the receipt is taken")
        self.assertEqual(self.calls, [], "by ID: no channel list")

    def test_not_shown_gives_4_and_why(self):
        self.bot({"ok": False, "why": "找不到頻道 2"})
        code, out, err = self.run_post({"channel": "2", "text": "x", "wait": 10, "notify": True})
        self.assertEqual(code, self.d.NOT_SENT)
        self.assertIn("沒有送出：找不到頻道 2", err)
        self.assertEqual(out, "")

    def test_without_wait_as_before(self):
        code, out, err = self.run_post({"channel": "2", "text": "x"})
        self.assertIn(code, (0, None))
        self.assertIn("已送出到 #2", out)
        (path, post), = postbox.waiting(self.spool)
        self.assertNotIn("receipt", post)

    def test_wait_only_for_messages_and_notifications(self):
        for post in ({"channel": "2", "board": "b", "text": "x"}, {"channel": "c", "new_claude": "/tmp/x"},
                     {"channel": "2", "move_to": "系統"}, {"channel": "2", "resume": "ab12"}):
            code, out, err = self.run_post(dict(post, wait=5))
            self.assertIn("--wait 只用在貼訊息和通知", str(code), post)
        code, out, err = self.run_post({"channel": "2", "text": "x", "wait": "soon"})
        self.assertIn("--wait 要給秒數", str(code))
        self.assertEqual(postbox.waiting(self.spool), [])


class Send(Base):
    def test_send_by_id_reads_no_list_and_prints_the_id(self):
        out = io.StringIO()
        with mock.patch("sys.stdout", out):
            self.d.main(["send", "2", "hello"])
        self.assertEqual(self.calls, [("POST", "/channels/2/messages")])
        self.assertEqual(out.getvalue().splitlines(), ["已送出", "777"])

    def send(self, *args):
        """(exit code, stdout, stderr) of dcctl send."""
        out, err, code = io.StringIO(), io.StringIO(), 0
        with mock.patch("sys.stdout", out), mock.patch("sys.stderr", err):
            try:
                self.d.main(["send"] + list(args))
            except SystemExit as e:
                code = e.code
        return code, out.getvalue(), err.getvalue()

    def test_mentions_ping_nobody_unless_asked(self):
        """C-89: a script's line that quotes <@42> or @everyone pings no one; --mentions (anywhere) pings the owner
        alone: never @everyone, a role or another user the text names (messaging review P0-1); no owner set, nobody."""
        self.send("2", "hi", "<@42>", "@everyone")
        self.assertEqual(self.bodies[-1], {"content": "hi <@42> @everyone", "allowed_mentions": {"parse": []}})
        with mock.patch.object(self.d.config, "OWNER_ID", 42):
            for args in (("2", "hi", "<@42>", "<@43>", "@everyone", "--mentions"), ("--mentions", "2", "hi", "<@42>", "<@43>", "@everyone")):
                code, out, err = self.send(*args)
                self.assertEqual(self.bodies[-1], {"content": "hi <@42> <@43> @everyone",
                                                   "allowed_mentions": {"parse": [], "users": ["42"]}}, args)
                self.assertEqual(out.splitlines(), ["已送出", "777"])
        with mock.patch.object(self.d.config, "OWNER_ID", 0):
            self.send("2", "hi", "<@42>", "--mentions")
            self.assertEqual(self.bodies[-1], {"content": "hi <@42>", "allowed_mentions": {"parse": []}})

    def test_over_2000_is_refused_never_cut(self):
        code, out, err = self.send("2", "字" * 2000)
        self.assertIn(code, (0, None))
        self.assertEqual(self.bodies[-1]["content"], "字" * 2000, "2000 goes, whole")
        self.calls.clear()
        for text in ("x" * 2001, "😀" * 1001):  # (Discord counts UTF-16 units: an emoji is two)
            code, out, err = self.send("2", text)
            self.assertEqual(code, 4, text[:3])
            self.assertIn("沒有送出：超過 2000 字", err)
            self.assertEqual((out, self.calls), ("", []), "nothing sent, no channel list read")
        code, out, err = self.send("project-a", "x" * 2001)
        self.assertEqual((code, self.calls), (4, []), "by name too: refused before the name is looked for")

    def test_no_channel_is_the_usage(self):
        code, out, err = self.send("--mentions")
        self.assertIn("用法：dcctl send", str(code))
        self.assertEqual(self.calls, [])


class Receipts(unittest.TestCase):
    """The bot's side: what it writes for a post that asked for a receipt."""
    def setUp(self):
        state = tempfile.TemporaryDirectory()
        self.addCleanup(state.cleanup)
        self.receipts = os.path.join(state.name, "receipts")
        p = mock.patch.object(postbox, "RECEIPTS", self.receipts)
        p.start()
        self.addCleanup(p.stop)
        self.bot = Bot()
        self.a, self.notes = Chan(1, "st-1"), Chan(3, "notes")
        for c in (self.a, self.notes):
            self.bot.chans[c.id] = c
        self.bot.guild.text_channels = [self.a, self.notes]
        self.bot.channels_state = {"1": {"pane": {"id": "%1"}}, "3": {"plain": True}}
        self.p = posts.Posts(self.bot)

    def handle(self, **post):
        post["receipt"] = "0123456789abcdef"
        with mock.patch.object(posts, "notify_default", return_value="notes"):
            asyncio.run(self.p.safely(post))
        return postbox.take_receipt("0123456789abcdef", self.receipts)

    def test_a_post_shown(self):
        got = self.handle(channel="notes", text="x", from_name="lap")
        self.assertEqual((got["ok"], got["message_id"]), (True, "1"))

    def test_a_notification_shown_and_one_redirected(self):
        self.assertTrue(self.handle(channel="@notify", text="x", notify=True)["ok"])
        got = self.handle(channel="no-such", text="x", notify=True)
        self.assertTrue(got["ok"], "it went to the default, saying where it was meant to go")
        self.assertEqual(got["message_id"], "2")

    def test_a_channel_not_found(self):
        got = self.handle(channel="no-such", text="x")
        self.assertEqual(got, dict(got, ok=False, why="找不到頻道 no-such"))

    def test_a_notification_that_only_reached_audit_is_not_shown(self):
        self.notes.fails = OSError("503")
        got = self.handle(channel="notes", text="x", notify=True)
        self.assertFalse(got["ok"])
        self.assertIn("寫進了 #audit", got["why"])

    def test_a_post_to_a_claude_is_receipted_once_shown_not_once_handed(self):
        handed = asyncio.Event()
        seen = []

        async def give(ch, sender, sender_ch, text, post):
            seen.append(postbox.take_receipt("0123456789abcdef", self.receipts))
            handed.set()
        with mock.patch.object(self.p, "give", give):
            self.assertIsNone(self.handle(channel="st-1", text="x", from_name="lap"), "taken during the hand-over")
        self.assertTrue(seen[0]["ok"], "written before it was handed to the Claude")

    def test_no_receipt_asked_none_written(self):
        with mock.patch.object(posts, "notify_default", return_value="notes"):
            asyncio.run(self.p.safely({"channel": "notes", "text": "x"}))
        self.assertFalse(os.path.exists(self.receipts))

    def test_a_token_of_another_shape_is_not_a_file_name(self):
        for bad in ("../x", "0123456789ABCDEF", "short", 7):
            postbox.write_receipt(bad, {"ok": True}, self.receipts)
        self.assertFalse(os.path.exists(self.receipts))


class CommandLine(unittest.TestCase):
    def test_dc_post_wait(self):
        dcpost = load("dcpost_wait", os.path.join(os.path.dirname(ROOT), "altention", "dc", "post.py"))
        self.assertEqual(dcpost.build(["notes", "--wait", "30", "x"], "", {}, "h", "/x")["wait"], 30)
        self.assertNotIn("wait", dcpost.build(["notes", "x"], "", {}, "h", "/x"))
        for argv in (["notes", "--wait", "0", "x"], ["notes", "--wait", "601", "x"], ["notes", "--wait", "x"],
                     ["notes", "--wait", "5", "--board", "b", "x"]):
            with self.assertRaises(SystemExit, msg=argv):
                dcpost.build(argv, "", {}, "h", "/x")


if __name__ == "__main__":
    unittest.main()
