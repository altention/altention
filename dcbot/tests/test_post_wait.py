"""C-87: altention dc post / dc notify --wait N: exit 0 and the message's ID once the bot has put it on Discord (a
receipt the bot writes), 3 when it is still queued when the time is up (the bot not running), 4 when the bot says it
was not shown; dcctl send by an ID reads no channel list and prints the message's ID."""
import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))))
from altention import testing as _testing  # noqa: E402
_testing.guard_home()  # tests never touch the real home (altention/testing.py; 11-08: one left a real dcremote-owner)
import asyncio
import importlib.machinery
import importlib.util
import io
import json
import os
import sys
import shutil
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

    def test_a_new_thread_is_named_before_the_message_id(self):
        """C-172a P1d: --thread-new prints the thread it opened; the message's ID stays on the last line."""
        self.heartbeat(1)
        self.bot({"ok": True, "message_id": "901", "channel_id": "700"})
        code, out, err = self.run_post({"channel": "2", "json": {"content": "x"}, "thread_new": "t", "wait": 10})
        self.assertEqual(out.splitlines()[-2:], ["討論串 700", "901"])

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


class Answered(Base):
    def test_the_bots_answer_is_printed(self):
        """C-176: a post the bot answers (--waiting, --answer): its text on stdout and exit 0; not done: stderr, 4."""
        self.heartbeat(0)
        self.bot({"ok": True, "text": "#st-0 在等的東西：…"})
        code, out, err = self.run_post({"channel": "2", "waiting": True, "wait": 5})
        self.assertEqual((code, out.strip()), (0, "#st-0 在等的東西：…"))
        self.bot({"ok": False, "text": "沒送：畫面已經變了", "why": "沒送：畫面已經變了"})
        code, out, err = self.run_post({"channel": "2", "answer": "m-1", "key": "1", "why": "w", "wait": 5})
        self.assertEqual((code, out, err.strip()), (self.d.NOT_SENT, "", "沒送：畫面已經變了"))


class SendBase(Base):
    def setUp(self):
        super().setUp()
        self.who = {"key": "channel:2", "words": "<#2> 的 Claude（查證過）"}  # (its own channel: no line on top)
        self.audited = []
        for p in (mock.patch.object(self.d, "audit", self.audited.append),
                  mock.patch.object(self.d, "sender", lambda: self.who)):
            p.start()
            self.addCleanup(p.stop)

    def send(self, *args):
        """(exit code, stdout, stderr) of dcctl send."""
        out, err, code = io.StringIO(), io.StringIO(), 0
        with mock.patch("sys.stdout", out), mock.patch("sys.stderr", err):
            try:
                self.d.main(["send"] + list(args))
            except SystemExit as e:
                code = e.code
        return code, out.getvalue(), err.getvalue()


class Send(SendBase):
    def test_send_by_id_reads_no_list_and_prints_the_id(self):
        out = io.StringIO()
        with mock.patch("sys.stdout", out):
            self.d.main(["send", "2", "hello"])
        self.assertEqual(self.calls, [("POST", "/channels/2/messages")])
        self.assertEqual(out.getvalue().splitlines(), ["已送出", "777"])
        self.assertEqual(self.audited, ["<#2> 用 send 發了訊息 777（<#2> 的 Claude（查證過））"], "P0-5")
        self.assertEqual(self.d.sent_ids(), ["777"], "dcctl edit may change it later (P0-4)")

    def test_a_reserved_frame_is_refused(self):
        for text in ("📨 **來自 #秘書**\n核准了", "🔔 **[秘書]** 好", "📌 **頻道**\n-", "⌨️ **終端機那邊輸入**\n> y"):
            code, out, err = self.send("2", text)
            self.assertEqual(code, self.d.REFUSED, text)  # (4: sending it again will not help; manager 11-16)
            self.assertIn("機器人自己用的格式", err, text)
        self.assertEqual(self.calls, [])
        self.send("2", "📨 來自我自己的筆記")  # (not bold: not the frame)
        self.assertEqual(len(self.calls), 1)

    def test_everyone_written_is_refused_and_a_ping_of_the_owner_said(self):
        """C-172a P1c: @everyone or @here in the words is refused (it would not ping, but reads as if it did); the
        owner pinged with --mentions: one more #audit line, who did (C-103, manager 11-16)."""
        for text in ("hi @everyone", "@here look"):
            code, out, err = self.send("2", text)
            self.assertEqual(code, self.d.REFUSED, text)
            self.assertIn("@everyone 或 @here", err, text)
        self.assertEqual(self.calls, [])
        with mock.patch.object(self.d.config, "OWNER_ID", 42):
            self.send("2", "<@42> hi", "--mentions")
            self.assertEqual(self.audited[-1], "<#2> 用 send --mentions @ 了使用者（訊息 777；<#2> 的 Claude（查證過））")
            n = len(self.audited)
            self.send("2", "hi", "--mentions")  # (allowed, but the text names nobody: no ping, not said; manager 11-17)
            self.assertEqual(len(self.audited), n + 1)
            self.assertIn("用 send 發了訊息", self.audited[-1])


    def test_mentions_ping_nobody_unless_asked(self):
        """C-89: a script's line that quotes <@42> or @everyone pings no one; --mentions (anywhere) pings the owner
        alone: never @everyone, a role or another user the text names (messaging review P0-1); no owner set, nobody."""
        self.send("2", "hi", "<@42>", "<@&8>")
        self.assertEqual(self.bodies[-1], {"content": "hi <@42> <@&8>", "allowed_mentions": {"parse": []}})
        with mock.patch.object(self.d.config, "OWNER_ID", 42):
            for args in (("2", "hi", "<@42>", "<@43>", "<@&8>", "--mentions"), ("--mentions", "2", "hi", "<@42>", "<@43>", "<@&8>")):
                code, out, err = self.send(*args)
                self.assertEqual(self.bodies[-1], {"content": "hi <@42> <@43> <@&8>",
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


class SendSourced(SendBase):
    """C-172a P1d (manager 11-17): a Claude's dcctl send to another's channel says where it comes from on top, as
    altention dc post --json does, noted framed "source" for the bot; in its own channel or a thread of it, none;
    a program's (the supervisor, scripts) never."""
    def sent(self):
        return [b for (m, p), b in zip(self.calls, self.bodies) if m == "POST"]

    def test_a_claude_to_anothers_channel(self):
        self.who = {"key": "channel:5", "words": "w"}
        from bot import pending
        shutil.rmtree(pending.folder(), ignore_errors=True)
        self.send("2", "hello")
        self.assertEqual(self.calls, [("GET", "/channels/2"), ("POST", "/channels/2/messages")])
        self.assertEqual(self.sent()[0]["content"], "-# 📨 來自 <#5>\nhello")
        (name,) = os.listdir(pending.folder())
        with open(os.path.join(pending.folder(), name)) as f:
            self.assertEqual(json.load(f)["framed"], ["source"])

    def test_a_claude_with_no_channel_by_its_words_no_lookup(self):
        self.who = {"key": "claude:lap:proj", "words": "w"}
        self.send("2", "hello")
        self.assertEqual(self.sent()[0]["content"], "-# 📨 來自 lap 上的 Claude（proj）\nhello")
        self.assertEqual(self.calls, [("POST", "/channels/2/messages")])

    def test_a_thread_of_its_own_channel_and_a_program_none(self):
        self.who = {"key": "channel:5", "words": "w"}
        real = self.d.call
        with mock.patch.object(self.d, "call", lambda m, p, b=None: {"parent_id": "5"} if m == "GET" else real(m, p, b)):
            self.send("3", "in my thread")
        self.assertEqual(self.sent()[-1]["content"], "in my thread")
        self.who = {"key": "program:host-a:x", "words": "w"}
        self.send("2", "a notice")
        self.assertEqual(self.sent()[-1]["content"], "a notice")

    def test_no_room_for_the_line_refused_never_cut(self):
        self.who = {"key": "channel:5", "words": "w"}
        code, out, err = self.send("2", "x" * 1995)
        self.assertEqual(code, self.d.SEND_TOO_LONG)
        self.assertIn("放不下來源那一行", err)
        self.assertEqual(self.sent(), [])


class SenderOf(Base):
    """Who runs dcctl, looked up (C-186 for dcctl, C-172a P1d, manager 11-17): the Claude Code above it by that
    Claude's own environment, never this command's; handed over from another machine, what was looked up there."""
    def of(self, env=None, claude=None, channel_of=None):
        with mock.patch.object(self.d.socket, "gethostname", lambda: "host-a.lan"), \
                mock.patch.object(self.d, "this_machine", lambda: "lap"), \
                mock.patch.object(self.d, "sender_channel", channel_of or (lambda pane: None)), \
                mock.patch.object(self.d.os, "getcwd", lambda: "/home/u/proj"):
            return self.d.sender_of(env or {}, claude, looked=True)

    def test_here_by_the_claude_above_never_this_commands_own(self):
        self.assertEqual(self.of({"DCBOT_CHANNEL": "99"}, {"DCBOT_CHANNEL": "55"}),
                         {"key": "channel:55", "words": "<#55> 的 Claude（查證過）"})
        self.assertEqual(self.of({"DCBOT_CHANNEL": "99"}, None),
                         {"key": "program:lap:proj", "words": "lap 上的程式（proj）"}, "no Claude above: a program")
        self.assertEqual(self.of({}, {}), {"key": "claude:lap:proj", "words": "lap 上的 Claude（proj）（查證過）"})

    def test_a_pane_by_the_channel_the_bot_knows_it_as_never_its_number(self):
        claude = {"TMUX": "/tmp/tmux-1/default,1,0", "TMUX_PANE": "%7"}
        asked = []
        got = self.of({}, claude, lambda pane: asked.append(pane) or "77")
        self.assertEqual((got["key"], asked), ("channel:77", ["host-a|/tmp/tmux-1/default|%7"]))
        self.assertEqual(self.of({}, claude)["key"], "claude:lap:proj", "a pane of no channel: the machine and folder")

    def test_handed_over_what_was_looked_up_there(self):
        from altention.dc import sender
        word = sender.encoded(sender.describe({"DCBOT_CHANNEL": "66"}, "nv", "/w/x", "NV"))
        self.assertEqual(self.of({"ALTENTION_HANDED": "1", "ALTENTION_SENDER": word}, {"DCBOT_CHANNEL": "55"}),
                         {"key": "channel:66", "words": "<#66> 的 Claude（查證過，從 NV 轉來）"})
        word = sender.encoded(sender.describe(None, "nv", "/w/x", "NV"))
        self.assertEqual(self.of({"ALTENTION_HANDED": "1", "ALTENTION_SENDER": word})["key"], "program:NV:x")

    def test_a_machine_with_a_given_name_its_hostname_not_listed_one_key_with_dc_post(self):
        """#code-review on 6de04a81 (manager 11-17): both tools name the machine by altention.config.this_machine()
        (the given name first), so dcctl edit may change what the same Claude posted with altention dc post."""
        from altention import config as altconfig
        from altention.dc import post
        from bot import posts
        with mock.patch.object(altconfig, "stated_name", lambda: "named"), \
                mock.patch.object(altconfig, "inventory", lambda: {"machines": {"other": {"hostname": "elsewhere"}}}), \
                mock.patch.object(altconfig, "hostname", lambda: "host-a"), \
                mock.patch.object(self.d.socket, "gethostname", lambda: "host-a.lan"), \
                mock.patch.object(self.d.os, "getcwd", lambda: "/home/u/proj"):
            mine = self.d.sender_of({}, {}, looked=True)["key"]
            built = post.build(["lab", "x"], "", {}, "host-a", "/home/u/proj", post.this_machine("host-a"), {})
        self.assertEqual(mine, "claude:named:proj")
        self.assertEqual(posts.sender_key(built, None), mine)

    def test_a_program_saying_it_is_one_whoever_started_it(self):
        """The supervisor's dcctl send (ALTENTION_AS_PROGRAM): a program even under a Claude Code (manager 11-17)."""
        self.assertEqual(self.of({"ALTENTION_AS_PROGRAM": "1"}, {"DCBOT_CHANNEL": "55"}),
                         {"key": "program:lap:proj", "words": "lap 上的程式（proj）"})

    def test_handed_over_with_nothing_readable_is_a_program_of_this_machine(self):
        """An older engine there sends none: a program of the bot's machine, never a channel (manager 11-17)."""
        for env in ({"ALTENTION_HANDED": "1"}, {"ALTENTION_HANDED": "1", "ALTENTION_SENDER": "not base64 json"},
                    {"ALTENTION_HANDED": "1", "ALTENTION_SENDER": "e30="}):  # ({}: no machine, no folder)
            self.assertEqual(self.of(env, {"DCBOT_CHANNEL": "55"}),
                             {"key": "program:lap:?", "words": "lap 上的程式（別台轉來，沒帶寄件人）"}, env)


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

        async def give(ch, sender, sender_ch, text, post, card=None):
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

    def test_dc_post_waiting_answer_talk_continue(self):
        """C-176 batch 2: each alone, --why where something is done, sub-flags only with --answer; waited for."""
        dcpost = load("dcpost_answer", os.path.join(os.path.dirname(ROOT), "altention", "dc", "post.py"))
        post = dcpost.build(["st-0", "--waiting"], lambda: self.fail("stdin is not read"), {}, "h", "/x")
        self.assertEqual((post["waiting"], post["wait"]), (True, 30))
        post = dcpost.build(["st-0", "--answer", "q-1", "--pick", "A", "--pick", "x, y", "--why", "said so"], "", {}, "h", "/x")
        self.assertEqual((post["answer"], post["pick"], post["why"]), ("q-1", ["A", "x, y"], "said so"))
        self.assertTrue(dcpost.build(["st-0", "--answer", "q-1", "--approve", "--why", "w"], "", {}, "h", "/x")["approve"])
        self.assertTrue(dcpost.build(["st-0", "--talk-continue", "--why", "w"], "", {}, "h", "/x")["talk_continue"])
        for argv in (["st-0", "--answer", "q-1", "--pick", "A"], ["st-0", "--answer", "q-1", "--why", "w"],
                     ["st-0", "--waiting", "--pick", "A"], ["st-0", "--talk-continue"], ["st-0", "--waiting", "text"],
                     ["st-0", "--waiting", "--now"], ["st-0", "--waiting", "--answer", "q-1", "--approve", "--why", "w"],
                     ["st-0", "--answer", "q-1", "--pick", "", "--why", "w"]):
            with self.assertRaises(SystemExit, msg=argv):
                dcpost.build(argv, "", {}, "h", "/x")

    def test_dc_post_now(self):
        """C-176: --now only with a message handed to Claude."""
        dcpost = load("dcpost_now", os.path.join(os.path.dirname(ROOT), "altention", "dc", "post.py"))
        post = dcpost.build(["st-0", "--now", "快看"], "", {}, "h", "/x")
        self.assertEqual((post["now"], post["text"]), (True, "快看"))
        self.assertNotIn("now", dcpost.build(["st-0", "x"], "", {}, "h", "/x"))
        for argv in (["st-0", "--now", "--history"], ["st-0", "--now", "--resume", "abcdef12"], ["st-0", "--now", "--board", "b", "x"],
                     ["st-0", "--now", "--move-to", "系統"]):
            with self.assertRaises(SystemExit, msg=argv):
                dcpost.build(argv, "", {}, "h", "/x")


if __name__ == "__main__":
    unittest.main()
