"""C-172a P1b batch 4: dcctl send-json and dcctl edit queued for the bot, which posts them through deliver()
(posts.from_dcctl, checked again there: the queue is files); dcctl send straight to Discord, its message taken into
the bot's ledger afterwards (bot/pending.py); #audit lines not kept; nothing in dcctl sends but send and its own
#audit line. P1c: the rules (bot/rules.py) checked again by the bot, an edit by the ledger, a tool's button answered,
dcctl pin kept in the ledger, and the pins made before taken in once (bot/pinimport.py)."""
import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))))
from altention import testing as _testing  # noqa: E402
_testing.guard_home()  # tests never touch the real home (altention/testing.py)
import asyncio
import importlib.machinery
import importlib.util
import io
import os
import re
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from unittest import mock

import discord

from bot import config, fromkeys, ledger, main, messages, pending, pinimport, postbox, posts, sentids
from tests import tmuxguard  # noqa: F401  (no test reaches a real tmux server)
from tests.test_posts import Bot

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DCCTL = os.path.join(ROOT, "tools", "dcctl")


def load_dcctl():
    loader = importlib.machinery.SourceFileLoader("dcctl_queue", DCCTL)
    mod = importlib.util.module_from_spec(importlib.util.spec_from_loader("dcctl_queue", loader))
    loader.exec_module(mod)
    return mod


class Http(object):
    def __init__(self, refuse=False):
        self.calls, self.refuse = [], refuse

    async def request(self, route, json=None, **kw):
        if self.refuse:
            raise discord.HTTPException(types.SimpleNamespace(status=400, reason="Bad Request"),
                                        {"code": 50035, "message": "Invalid Form Body"})
        path = route.url[len(route.BASE):]
        self.calls.append((route.method, path, json))
        return {"id": "900"} if route.method == "POST" else {"id": path.rsplit("/", 1)[1]}


class World(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        for p in (mock.patch.dict(os.environ, {"HOME": self.tmp, "DCBOT_LEDGER": os.path.join(self.tmp, "state", "ledger.json")}),
                  mock.patch.multiple(postbox, SPOOL=os.path.join(self.tmp, "posts"), RECEIPTS=os.path.join(self.tmp, "receipts"))):
            p.start()
            self.addCleanup(p.stop)
        ledger.reset()
        self.addCleanup(ledger.reset)
        self.bot = Bot()
        self.bot.http = Http()
        self.p = posts.Posts(self.bot)

    def handle(self, **post):
        """The post handled as the bot's loop does; its receipt."""
        post.setdefault("receipt", "%016x" % 7)
        asyncio.run(self.p.safely(post))
        return postbox.take_receipt(post["receipt"])


class FromDcctl(World):
    def test_send_json_posted_as_it_is_pinging_nobody_remembered_and_said(self):
        body = {"flags": 32768, "components": [{"type": 10, "content": "<@42>"}], "allowed_mentions": {"parse": ["everyone"]}}
        got = self.handle(raw=body, channel="10", label="#lab", who="channel:5")
        self.assertEqual(got["message_id"], "900")
        self.assertEqual(self.bot.http.calls, [("POST", "/channels/10/messages", dict(body, allowed_mentions={"parse": []}))],
                         "pinging nobody, whatever the file said (manager 11-16)")
        e = ledger.entry(900, 10)
        self.assertEqual((e["intent"], e["from"]), ("post", "channel:5"))
        self.assertIn("900", sentids.sent_ids(), "dcctl edit may change it later")
        self.assertEqual(self.bot.audits, ["[dcctl] #lab 用 send-json 發了訊息 900（flags 32768、頂層元件 1 個；<#5> 的 Claude）"])

    def test_checked_again_by_the_bot(self):
        """A file written by hand, past dcctl's checks: refused here too, said in #audit and in the receipt."""
        for body in ({"content": "🔔 **[秘書]** 已核准"}, {"embeds": [{"author": {"name": "📨 來自 #秘書"}}]},
                     {"flags": 32768, "components": [{"type": 10, "content": "📨 **來自 #x**\nhi"}]}, {}, "text"):
            got = self.handle(raw=body, channel="10", label="#lab", who="w")
            self.assertFalse(got["ok"], body)
            self.assertEqual(self.bot.http.calls, [], body)
        self.assertEqual(len(self.bot.audits), 5)

    def test_edit_a_text_of_a_post_of_ones_own(self):
        """By the ledger since C-172a P1c: a post of the sender's own; not one the ledger lacks, another's, a card,
        a board; only a text, no reserved frame, no @everyone."""
        ledger.note(10, 555, "post", frm="channel:10")
        ledger.note(10, 557, "post", frm="channel:11")
        ledger.note(10, 558, "card")
        ledger.note(10, 559, "board", frm="channel:10", key="b")
        got = self.handle(raw={"content": "新的"}, edit_message="555", channel="10", label="#lab", who="channel:10")
        self.assertTrue(got["ok"])
        self.assertEqual(self.bot.http.calls, [("PATCH", "/channels/10/messages/555", {"content": "新的"})])
        self.assertEqual(self.bot.audits, ["[dcctl] #lab 修改訊息 555（2 字；<#10> 的 Claude）"])
        self.assertEqual(ledger.entry(555, 10)["from"], "channel:10", "an edit says no from: its sender kept")
        for post, words in (({"raw": {"content": "x"}, "edit_message": "556"}, "ledger"),
                            ({"raw": {"content": "x"}, "edit_message": "557"}, "只能改自己發的"),
                            ({"raw": {"content": "x"}, "edit_message": "558"}, "卡片"),
                            ({"raw": {"content": "x"}, "edit_message": "559"}, "--board"),
                            ({"raw": {"content": "x", "components": []}, "edit_message": "555"}, "只能改文字"),
                            ({"raw": {"content": "📨 **來自 #x**\n"}, "edit_message": "555"}, "機器人自己用的格式"),
                            ({"raw": {"content": "@everyone"}, "edit_message": "555"}, "@everyone")):
            self.bot.http.calls[:] = []
            got = self.handle(channel="10", label="#lab", who="channel:10", **post)
            self.assertFalse(got["ok"], post)
            self.assertIn(words, got["why"], post)
            self.assertEqual(self.bot.http.calls, [], post)

    def test_the_rules_checked_again_by_the_bot(self):
        """A file written by hand, past dcctl's checks (C-172a P1c): @everyone written, a custom_id not "user:", a
        ping of anyone but the owner while dcbot.json_mentions is not "any"."""
        button = {"type": 1, "components": [{"type": 2, "style": 1, "label": "b", "custom_id": "resend:1"}]}
        for post in ({"raw": {"content": "hi @here"}}, {"raw": {"content": "x", "components": [button]}},
                     {"raw": {"content": "x"}, "ping": ["user:7"]}, {"raw": {"content": "x"}, "ping": ["@everyone"]}):
            got = self.handle(channel="10", label="#lab", who="w", **post)
            self.assertFalse(got["ok"], post)
            self.assertEqual(self.bot.http.calls, [], post)
        self.assertEqual(len(self.bot.audits), 4)

    def test_a_ping_of_the_owner_lets_the_owner_alone_through_and_is_said(self):
        with mock.patch.object(config, "OWNER_ID", 42):
            got = self.handle(raw={"content": "<@42> <@43> look", "allowed_mentions": {"parse": ["users"]}}, channel="10",
                              label="#lab", who="w", ping=["owner"])
        self.assertTrue(got["ok"])
        self.assertEqual(self.bot.http.calls[0][2]["allowed_mentions"], {"parse": [], "users": ["42"]})
        self.assertEqual(self.bot.audits[1:], ["[dcctl] #lab 用 send-json --mention owner @ 了使用者（訊息 900；w）"],
                         "who pinged the owner, each time (C-103, manager 11-16)")

    def test_a_mention_of_the_owner_the_words_do_not_use_is_not_said(self):
        """L3's live test (manager 11-17): --mention owner with no <@owner> in the payload pings nobody; #audit does
        not say it did. In a V2 Text Display it pings, and is said."""
        with mock.patch.object(config, "OWNER_ID", 42):
            self.handle(raw={"content": "look"}, channel="10", label="#lab", who="w", ping=["owner"])
            self.assertEqual(len(self.bot.audits), 1)
            v2 = {"flags": 32768, "components": [{"type": 17, "components": [{"type": 10, "content": "<@!42> look"}]}]}
            self.handle(raw=v2, channel="10", label="#lab", who="w", ping=["owner"])
        self.assertIn("@ 了使用者", self.bot.audits[-1])

    def test_a_user_or_a_role_when_the_instance_says_any(self):
        with mock.patch.dict(messages.loaded, json_mentions="any"):
            got = self.handle(raw={"content": "x"}, channel="10", label="#lab", who="w", ping=["user:7", "role:8"])
        self.assertTrue(got["ok"])
        self.assertEqual(self.bot.http.calls[0][2]["allowed_mentions"], {"parse": [], "users": ["7"], "roles": ["8"]})
        self.assertEqual(len(self.bot.audits), 1, "only the owner's ping has a line of its own")

    def test_what_discord_refuses_goes_back_in_the_receipt(self):
        self.bot.http = Http(refuse=True)
        got = self.handle(raw={"content": "x"}, channel="10", label="#lab", who="w")
        self.assertFalse(got["ok"])
        self.assertIn("Discord 沒收（400 50035）", got["why"])
        self.assertEqual(self.bot.audits, [])


class Pending(World):
    def dcctl_send(self, dcctl, *words):
        calls = []

        def call(method, path, body=None):
            calls.append((method, path, body))
            return {"id": "901"} if path == "/channels/10/messages" else {}
        chans = [{"id": "10", "name": "lab", "type": 0}]
        with mock.patch.object(dcctl, "call", call), mock.patch.object(dcctl, "channels", lambda *a, **k: chans), \
                mock.patch.object(dcctl, "sender", lambda: {"key": "program:lap:x", "words": "lap 上的程式（x）"}), mock.patch("sys.stdout", io.StringIO()):
            dcctl.main(["send", "lab"] + list(words))
        return calls

    def test_dcctl_send_is_taken_into_the_ledger_by_the_bot(self):
        """The file is written by dcctl send itself (the writer), read by the bot's round (posts.tick)."""
        dcctl = load_dcctl()
        calls = self.dcctl_send(dcctl, "hello")
        self.assertEqual(calls[0][:2], ("POST", "/channels/10/messages"), "still straight to Discord")
        (name,) = os.listdir(pending.folder())
        self.assertIsNone(ledger.entry(901, 10), "not yet: the bot's to write")
        asyncio.run(self.p.tick())
        e = ledger.entry(901, 10)
        self.assertEqual((e["intent"], e["from"]), ("post", "program:lap:x"))
        self.assertEqual(os.listdir(pending.folder()), [], "taken in once")

    def test_a_file_that_cannot_be_read_is_put_aside_once(self):
        os.makedirs(pending.folder())
        for name, text in (("1-1.json", "{half"), ("2-1.json", '{"channel": "x"}')):
            with open(os.path.join(pending.folder(), name), "w") as f:
                f.write(text)
        pending.write(10, 902, "w")
        self.assertEqual(pending.drain(ledger.note), 1)
        self.assertEqual(os.listdir(pending.folder()), [])
        self.assertEqual(sorted(os.listdir(pending.bad_folder())), ["1-1.json", "2-1.json"])
        self.assertEqual(pending.drain(ledger.note), 0, "not read again each round")
        self.assertEqual(ledger.entry(902, 10)["from"], "w")

    def test_written_whole(self):
        with mock.patch("os.rename", side_effect=OSError("disk")):
            with self.assertRaises(OSError):
                pending.write(10, 903, "w")
        self.assertEqual([n for n in os.listdir(pending.folder()) if not n.startswith(".")], [], "never half a file to read")


class BeforeTheTurn(World):
    """The words the ledger wrote before P1d, until bot/fromkeys.py turns them (manager 11-17): an edit of one's own
    post is not refused meanwhile (exit 4 would tell a Claude not to try again), and an older dcctl's note taken in
    after the turn gets its key."""
    def test_an_edit_of_ones_own_post_noted_in_the_old_words_goes(self):
        ledger.note(10, 555, "post", frm="頻道 10 的 Claude（自稱）")  # (in its own channel)
        got = self.handle(raw={"content": "新的"}, edit_message="555", channel="10", label="#lab", who="channel:10")
        self.assertTrue(got["ok"], got)
        got = self.handle(raw={"content": "x"}, edit_message="555", channel="10", label="#lab", who="channel:6")
        self.assertIn("只能改自己發的", got["why"], "another's still refused")

    def test_a_name_by_the_servers_channels_when_known(self):
        ledger.note(10, 556, "post", frm="#lab")
        got = self.handle(raw={"content": "x"}, edit_message="556", channel="10", label="#lab", who="channel:10")
        self.assertFalse(got["ok"], "no server known: a name is not guessed")
        self.bot.get_guild = lambda gid: types.SimpleNamespace(text_channels=[types.SimpleNamespace(name="lab", id=10)])
        got = self.handle(raw={"content": "x"}, edit_message="556", channel="10", label="#lab", who="channel:10")
        self.assertTrue(got["ok"], got)

    def test_an_older_dcctls_note_taken_in_after_the_turn_gets_its_key(self):
        os.makedirs(os.path.dirname(fromkeys.mark_path()), exist_ok=True)
        open(fromkeys.mark_path(), "w").close()  # (turned already)
        self.bot.get_guild = lambda gid: types.SimpleNamespace(text_channels=[types.SimpleNamespace(name="lab", id=10)])
        pending.write(10, 901, "頻道 5 的 Claude（自稱）")
        pending.write(10, 902, "CGI 的 tmux 窗格 %3（自稱）")
        asyncio.run(self.p.tick())
        self.assertEqual((ledger.entry(901, 10)["from"], ledger.entry(902, 10)["from"]),
                         ("channel:5", "CGI 的 tmux 窗格 %3（自稱）"), "as the turn would: a pane's number is left")


class PendingFramed(World):
    def test_a_line_on_top_noted_by_dcctl_send_reaches_the_ledger(self):
        """#code-review-2 on 0e097753: dcctl send's framed source (its line on top in another's channel) goes into the
        ledger with the bot's round, so an edit puts the line back."""
        pending.write(10, 903, "channel:5", framed=("source",))
        pending.write(10, 904, "program:h:x")
        asyncio.run(self.p.tick())
        self.assertEqual((ledger.entry(903, 10)["framed"], ledger.entry(904, 10)["framed"]), (["source"], []))


class TurnedInTheRound(World):
    """posts.tick turns the ledger's old senders into keys (bot/fromkeys.py) once the server is known, before
    dcctl send's notes are taken in and before any queued post is compared with them (#code-review-2 on 6de04a81)."""
    def test_turned_in_the_round_first(self):
        os.makedirs(os.path.dirname(ledger.path()), exist_ok=True)  # (not there: not written, no mark, tried again)
        ledger.note(10, 555, "post", frm="頻道 5 的 Claude（自稱）")
        self.bot.get_guild = lambda gid: types.SimpleNamespace(text_channels=[])
        order = []
        once, drain = fromkeys.once, pending.drain

        async def first(bot, gid):
            order.append("turn")
            await once(bot, gid)
        with mock.patch.object(fromkeys, "once", first), \
                mock.patch.object(pending, "drain", lambda *a: order.append("drain") or drain(*a)):
            asyncio.run(self.p.tick())
            asyncio.run(self.p.tick())
        self.assertEqual(order, ["turn", "drain", "drain"], "first, and once")
        self.assertEqual(ledger.entry(555, 10)["from"], "channel:5")
        self.assertTrue(fromkeys.done())
        self.assertIn("寄件人改成固定的鍵：轉了 1 則", self.bot.audits[-1])

    def test_not_before_the_server_is_known(self):
        ledger.note(10, 555, "post", frm="頻道 5 的 Claude（自稱）")
        self.bot.get_guild = lambda gid: None
        asyncio.run(self.p.tick())
        self.assertFalse(fromkeys.done())
        self.assertEqual(ledger.entry(555, 10)["from"], "頻道 5 的 Claude（自稱）")


class PendingPin(World):
    def test_dcctl_pin_is_taken_into_the_ledger_by_the_bot(self):
        """dcctl pin leaves a file, the bot's round marks the entry pinned (ledger.pin()); --unpin takes it off; a
        message the ledger does not have is left alone. dcctl-pinned.json is no longer written."""
        dcctl = load_dcctl()
        ledger.note(10, 555, "post", frm="w")
        chans = [{"id": "10", "name": "lab", "type": 0}]
        for argv, want, said in ((["pin", "lab", "555"], True, ["已釘選"]), (["pin", "lab", "555", "--unpin"], None, ["已取消釘選"]),
                                 (["pin", "lab", "556"], None, ["已釘選", "這則不在機器人的紀錄裡，釘了也不能用 dcctl edit 改"
                                                                 "（剛用 dcctl send 發的除外：機器人幾秒內會記上）"])):
            os.makedirs(os.path.dirname(ledger.path()), exist_ok=True)
            ledger.save()  # (dcctl reads the ledger's file)
            out = io.StringIO()
            with mock.patch.object(dcctl, "call", lambda *a, **k: {}), mock.patch.object(dcctl, "channels", lambda *a, **k: chans), \
                    mock.patch.object(dcctl, "audit", lambda line: None), mock.patch("sys.stdout", out):
                dcctl.main(argv)
            self.assertEqual(out.getvalue().splitlines(), said, "#code-review on 0ae18afc: one not on record, said")
            asyncio.run(self.p.tick())
            self.assertEqual(ledger.entry(555, 10).get("pinned"), want, argv)
        self.assertIsNone(ledger.entry(556, 10))
        self.assertFalse(os.path.exists(sentids.pinned_path()))


class Msg(object):
    def __init__(self, mid, author=1):
        self.id, self.author = mid, types.SimpleNamespace(id=author)
        self.created_at = None


class Chan(object):
    def __init__(self, cid, pins, fails=False):
        self.id, self._pins, self.fails, self.asked = cid, pins, fails, 0

    async def pins(self):
        self.asked += 1
        if self.fails:
            raise discord.Forbidden(types.SimpleNamespace(status=403, reason="Forbidden"), "no")
        return self._pins


class PinImport(World):
    """Manager 11-16: the dcctl messages pinned before P1c (dcctl-sent.json says no channel; dcctl-pinned.json, the
    old list) taken into the ledger once, by the pins Discord has, as a post of that channel's Claude, pinned."""
    def go(self, chans):
        lines = []

        async def audit(text):
            lines.append(text)

        async def no_wait(_secs):
            pass
        got = asyncio.run(pinimport.run(chans, 1, audit, sleep=no_wait))
        return got, lines

    def test_pinned_and_dcctls_only_as_that_channels_claude(self):
        for mid in ("500", "501", "502"):
            sentids.remember_sent(mid)
        with open(sentids.pinned_path(), "w") as f:
            f.write('["503"]')
        chans = [Chan(10, [Msg(500), Msg(600), Msg(502, author=9)]), Chan(11, [Msg(503)]), Chan(12, [], fails=True)]
        got, lines = self.go(chans)
        self.assertEqual(got, {10: 1, 11: 1})
        self.assertEqual(ledger.entry(500, 10), dict(ledger.entry(500, 10), intent="post", pinned=True,
                                                      **{"from": "channel:10"}))
        self.assertEqual(ledger.entry(503, 11)["from"], "channel:11")
        for mid, cid in ((600, 10), (502, 10), (501, 10)):
            self.assertIsNone(ledger.entry(mid, cid), "not dcctl's, not the bot's, or not pinned: not taken in")
        self.assertTrue(pinimport.done())
        self.assertFalse(os.path.exists(sentids.pinned_path()))
        self.assertTrue(os.path.exists(sentids.pinned_path() + ".merged"), "renamed aside, not deleted")
        self.assertEqual(len(lines), 1)
        self.assertIn("2 則", lines[0])

    def test_a_failure_but_no_permission_or_gone_is_tried_again_next_start(self):
        """#code-review-2 on 0ae18afc: only Forbidden and NotFound are "nothing pinned there"; Discord or the network
        failing leaves no mark and the old list where it was, so the next start does it all again."""
        sentids.remember_sent("500")
        with open(sentids.pinned_path(), "w") as f:
            f.write('["503"]')

        class Broken(Chan):
            async def pins(self):
                raise discord.HTTPException(types.SimpleNamespace(status=503, reason="Service Unavailable"), "down")
        gone = Chan(12, [])

        async def not_found():
            raise discord.NotFound(types.SimpleNamespace(status=404, reason="Not Found"), "gone")
        gone.pins = not_found
        with self.assertRaises(discord.HTTPException):
            self.go([Chan(10, [Msg(500)]), Broken(11, [])])
        self.assertFalse(pinimport.done())
        self.assertTrue(os.path.exists(sentids.pinned_path()), "not renamed")
        got, lines = self.go([Chan(10, [Msg(500)]), gone, Chan(13, [], fails=True)])
        self.assertEqual(got, {10: 1})
        self.assertTrue(pinimport.done())

    def test_one_already_on_record_keeps_its_sender(self):
        sentids.remember_sent("500")
        ledger.note(10, 500, "post", frm="channel:5")
        self.go([Chan(10, [Msg(500)])])
        self.assertEqual(ledger.entry(500, 10)["from"], "channel:5")
        self.assertTrue(ledger.entry(500, 10)["pinned"])

    def test_the_channels_claude_may_edit_it_others_may_not(self):
        sentids.remember_sent("500")
        self.go([Chan(10, [Msg(500)])])
        got = self.handle(raw={"content": "x"}, edit_message="500", channel="10", label="#lab", who="channel:6")
        self.assertFalse(got["ok"])
        got = self.handle(raw={"content": "x"}, edit_message="500", channel="10", label="#lab", who="channel:10")
        self.assertTrue(got["ok"])
        self.assertTrue(ledger.entry(500, 10)["pinned"], "an edit keeps the pin")

    def test_not_pushed_out_by_500_more(self):
        sentids.remember_sent("500")
        self.go([Chan(10, [Msg(500)])])
        for i in range(ledger.PER_CHANNEL + 10):
            ledger.note(10, 10000 + i, "reply")
        self.assertTrue(ledger.entry(500, 10)["pinned"])
        ledger.pin(10, 500, False)
        self.assertIsNone(ledger.entry(500, 10), "unpinned: as old as it is, gone with the limit")

    def test_once_and_in_the_bots_round_only_when_it_is_logged_in(self):
        self.go([])
        self.assertTrue(pinimport.done(), "nothing to take in is done too")
        self.assertIsNone(self.p.pins_asked)
        os.remove(pinimport.mark_path())
        asyncio.run(self.p.tick())
        self.assertIsNone(self.p.pins_asked, "not logged in yet (no user): not started")


class PinImportStarted(World):
    def test_the_servers_text_channels_slowly_once(self):
        sentids.remember_sent("500")
        chans = [Chan(10, [Msg(500)]), Chan(11, [])]
        bot = types.SimpleNamespace(user=types.SimpleNamespace(id=1), audits=[],
                                    get_guild=lambda gid: types.SimpleNamespace(text_channels=chans))

        async def audit(text):
            bot.audits.append(text)
        bot.audit = audit
        gaps = []

        async def gap(secs):
            gaps.append(secs)
        with mock.patch.object(pinimport.asyncio, "sleep", gap):
            asyncio.run(pinimport.once(bot, 7))
            asyncio.run(pinimport.once(bot, 7))
        self.assertEqual([c.asked for c in chans], [1, 1], "once: the mark is there after")
        self.assertEqual(gaps, [pinimport.GAP] * 2, "a gap after each channel (Discord's limits)")
        self.assertEqual(len(bot.audits), 1)
        self.assertTrue(ledger.entry(500, 10)["pinned"])


class UserButton(unittest.TestCase):
    def test_a_tools_button_is_answered_to_the_one_who_pressed_it(self):
        """C-172a P1c: a custom_id "user:…" is taken by nothing yet; said, only to the one who pressed it, before any
        handler of the bot's is asked (none is made here: one asked would fail)."""
        b = types.SimpleNamespace()
        for name in ("on_interaction", "user_button"):
            setattr(b, name, types.MethodType(getattr(main.Bot, name), b))
        said = []
        response = mock.Mock(send_message=mock.AsyncMock(side_effect=lambda t, ephemeral: said.append((t, ephemeral))))
        pressed = types.SimpleNamespace(type=discord.InteractionType.component, data={"custom_id": "user:go"},
                                        response=response, user=types.SimpleNamespace(id=7))
        asyncio.run(b.on_interaction(pressed))
        self.assertEqual(said, [(main.USER_BUTTON_SAID, True)])


class NotKept(World):
    def test_to_audit_is_not_kept(self):
        """#code-review-2 on 179b5a9c: posts.to_audit's lines, as bot.audit's, go out unrecorded."""
        self.assertTrue(asyncio.run(self.p.to_audit("a line")))
        self.assertEqual(self.bot.audit_ch.sent, ["a line"])
        self.assertEqual(ledger._loaded()["channels"], {})


class SentIdsLocked(unittest.TestCase):
    """#code-review on e41c4b2e: dcctl-sent.json has two writers now, the bot (send-json) and dcctl (send); each reads,
    changes and writes it back under one lock, so two at once lose no ID (dcctl edit would refuse a lost one)."""
    WRITER = ("import sys; sys.path[:0] = [%r]\nfrom bot import sentids\n"
              "for i in range(int(sys.argv[2])): sentids.remember_sent('%%s%%03d' %% (sys.argv[1], i))") % ROOT

    def test_two_processes_at_once_lose_no_id(self):
        home = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, home, True)
        env = dict(os.environ, HOME=home)
        runs = [subprocess.Popen([sys.executable, "-c", self.WRITER, tag, "150"], env=env) for tag in ("1", "2")]
        self.assertEqual([r.wait(timeout=60) for r in runs], [0, 0])
        with mock.patch.dict(os.environ, {"HOME": home}):
            got = sentids.sent_ids()
        self.assertEqual(len(got), 300, "every ID of both writers kept")
        self.assertEqual(len(set(got)), 300)


class DcctlOneWay(unittest.TestCase):
    """What dcctl still posts or patches on Discord itself, each with its reason (manager 11-16): send, and dcctl's
    own #audit line. send-json and edit go through the bot."""
    ALLOWED = {
        'm = call("POST", "/channels/%s/messages" % cid, body) or {}': "dcctl send: straight to Discord, the supervisor "
                                                                         "tells the owner through it while the bot is down",
        'call("POST", "/channels/%s/messages" % c["id"], {"content": "[dcctl] " + text[:1900], "allowed_mentions": {"parse": []}})':
            "dcctl's own #audit line of what it did (channels, pins, send): written where it ran, the bot may be down",
        'call("PATCH", "/channels/%s/messages/%s" % (ch["id"], mid), {"content": content})': "dcctl guide: the guide "
            "channel's text, kept up to date by deploy.sh while the bot restarts; not chat",
        'call("DELETE", "/channels/%s/messages/%s" % (ch["id"], mid))': "dcctl guide, as above",
        'call("POST", "/channels/%s/messages" % ch["id"], {"content": content[:2000], "flags": 4})':
            "dcctl guide, as above",
    }

    def test_nothing_else_posts_or_patches_a_message(self):
        with open(DCCTL, encoding="utf-8") as f:
            lines = [re.sub(r"\s+# .*$", "", ln).strip() for ln in f if re.search(r'call\("(POST|PATCH|DELETE)", "/channels/[^"]*messages', ln)]
        self.assertEqual(sorted(lines), sorted(self.ALLOWED), "a message goes through the bot's queue, or needs its reason here")


if __name__ == "__main__":
    unittest.main()
