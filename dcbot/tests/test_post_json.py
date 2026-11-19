"""C-172a P1d: altention dc post --json (bot/posts.py from_json, bot/payloads.py): one message's payload posted as it
is through the bot's queue, by P1c's rules; the line saying where it comes from on top in another's channel, never in
the sender's own (nor a thread of it, nor its parent from one); --edit of one's own, --thread-new, --reply-to,
--silent, --no-embeds, --mention, --file; noted in the ledger, said in #audit, never handed to a Claude. And --reply-to
and --silent on a plain post."""
import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))))
from altention import testing as _testing  # noqa: E402
_testing.guard_home()  # tests never touch the real home (altention/testing.py)
import asyncio
import base64
import os
import shutil
import tempfile
import types
import unittest
from unittest import mock

import discord

from bot import config, frames, ledger, payloads, postbox, posts
from tests import tmuxguard  # noqa: F401  (no test reaches a real tmux server)
from tests.test_posts import Bot, Chan

V2 = {"flags": 32768, "components": [{"type": 17, "components": [{"type": 10, "content": "表"}]}]}


class Http(object):
    """discord.py's HTTP client as from_json uses it: each request kept (method, path, json, the files' names)."""
    def __init__(self):
        self.calls, self.refuse, self.gone, self.pins, self.next = [], None, set(), set(), 900
        self.refuse_posts = None  # (a POST Discord refuses: an HTTPException of this status)
        self.archived, self.locked = set(), set()  # (threads gone to sleep; ones the bot cannot open again)

    async def request(self, route, json=None, files=None, form=None, **kw):
        if self.refuse:
            raise discord.HTTPException(types.SimpleNamespace(status=400, reason="Bad Request"), self.refuse)
        path = route.url[len(route.BASE):]
        parts = path.split("/")  # ("", "channels", id, …)
        if route.method == "PATCH" and len(parts) == 3 and json == {"archived": False}:
            self.calls.append(("PATCH", path, json, []))
            if parts[2] in self.locked:
                raise discord.Forbidden(types.SimpleNamespace(status=403, reason="Forbidden"), {"code": 50013, "message": "Missing Permissions"})
            self.archived.discard(parts[2])
            return {"id": parts[2]}
        if route.method == "PATCH" and len(parts) > 3 and parts[2] in self.archived:
            self.calls.append(("PATCH", path, json, []))
            raise discord.HTTPException(*ARCHIVED_ANSWER)
        if route.method == "GET":
            self.calls.append(("GET", path, None, []))
            return {"id": path.rsplit("/", 1)[1], "pinned": path.rsplit("/", 1)[1] in self.pins}
        if route.method == "PATCH" and path.rsplit("/", 1)[1] in self.gone:
            self.calls.append(("PATCH", path, json, []))
            raise discord.NotFound(types.SimpleNamespace(status=404, reason="Not Found"), {"code": 10008, "message": "Unknown Message"})
        if form:
            import json as js
            json = js.loads(form[0]["value"])
        self.calls.append((route.method, path, json, [f.filename for f in files or ()]))
        if path.endswith("/threads"):
            return {"id": "700"}
        if route.method == "POST" and self.refuse_posts and not path.endswith("/threads"):
            self.calls.append(("POST", path, json, []))
            raise discord.HTTPException(types.SimpleNamespace(status=self.refuse_posts, reason="x"), {"code": 0, "message": "x"})
        if route.method == "POST":
            self.next += 1
            return {"id": str(self.next - 1)}
        return {"id": path.rsplit("/", 1)[1]}


# Discord's answer to an edit in a thread gone to sleep, as L3 got it on the live server (11-17, C-191 live test 6:
# "Discord 沒收（400 50083）：Thread is archived")
ARCHIVED_ANSWER = (types.SimpleNamespace(status=400, reason="Bad Request"), {"code": 50083, "message": "Thread is archived"})


class Replying(Chan):
    async def send(self, text=None, reference=None, **kw):
        self.refs = getattr(self, "refs", []) + [(reference, kw.get("silent"))]
        return await super().send(text, **kw)

    def get_partial_message(self, mid):
        chan = self

        class Partial(object):
            id = mid

            async def delete(self):
                chan.deleted = getattr(chan, "deleted", []) + [mid]
        return Partial()


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        for p in (mock.patch.dict(os.environ, {"DCBOT_LEDGER": os.path.join(self.tmp, "ledger.json")}),
                  mock.patch.multiple(postbox, SPOOL=os.path.join(self.tmp, "posts"), RECEIPTS=os.path.join(self.tmp, "r")),
                  mock.patch.object(config, "OWNER_ID", 42)):
            p.start()
            self.addCleanup(p.stop)
        ledger.reset()
        self.addCleanup(ledger.reset)
        self.bot = Bot()
        self.bot.http = Http()
        self.a, self.b = Replying(1, "st-1"), Replying(2, "lab")
        self.t = Replying(3, "a-thread")
        self.t.parent_id = 1  # (a thread of st-1)
        for c in (self.a, self.b, self.t):
            self.bot.chans[c.id] = c
        self.bot.guild.text_channels = [self.a, self.b]
        self.bot.channels_state = {"1": {"tmux": "dc-1"}, "2": {"tmux": "dc-2"}}
        self.p = posts.Posts(self.bot)

    def post(self, **kw):
        kw.setdefault("receipt", "%016x" % 7)
        kw.setdefault("verified", True)
        asyncio.run(self.p.safely(kw))
        return postbox.take_receipt(kw["receipt"])

    def sent(self):
        return [c for c in self.bot.http.calls]


class Json(Base):
    def test_to_anothers_channel_with_where_it_comes_from_on_top_never_handed(self):
        got = self.post(channel="2", json={"content": "hi"}, from_channel="1")
        self.assertTrue(got["ok"], got)
        (method, path, body, files), = self.sent()
        self.assertEqual((method, path), ("POST", "/channels/2/messages"))
        self.assertEqual(body["content"], "-# 📨 來自 <#1>\nhi")
        self.assertEqual(body["allowed_mentions"], {"parse": []})
        self.assertEqual(self.bot.tmux.given, [], "a post: never handed to the channel's Claude")
        e = ledger.entry(900, 2)
        self.assertEqual((e["intent"], e["from"], e["framed"]), ("post", "channel:1", ["source"]))
        self.assertEqual(self.bot.audits, ["[dc post --json] #lab 發了訊息 900（flags 無、頂層元件 0 個；#st-1（查證過））"])
        what = frames.of(body["content"], by_bot=True, entry=e, name_of=lambda cid: self.bot.chans[cid].name)
        self.assertEqual((what["sender"], what["text"]), ("#st-1", "hi"), "read back: the line off, the sender named")

    def test_in_its_own_channel_and_threads_no_line(self):
        for to, frm in (("1", "1"), ("3", "1")):
            self.bot.http.calls[:] = []
            self.post(channel=to, json={"content": "hi"}, from_channel=frm)
            self.assertEqual(self.sent()[0][2]["content"], "hi", (to, frm))
        self.bot.channels_state["3"] = {}
        self.bot.http.calls[:] = []
        self.post(channel="1", json={"content": "hi"}, from_channel="3")
        self.assertEqual(self.sent()[0][2]["content"], "hi", "from a thread back to its channel")

    def test_a_sender_with_no_channel_in_words_and_v2_by_a_text_display(self):
        self.post(channel="2", json=V2, from_host="nv", from_machine="NV", from_folder="q",
                  from_name="nv 上的 Claude（q）")
        body = self.sent()[0][2]
        self.assertEqual(body["components"][0], {"type": 10, "content": "-# 📨 來自 NV 上的 Claude（q）"})
        self.assertEqual(body["components"][1:], V2["components"])
        self.assertEqual(ledger.entry(900, 2)["from"], "claude:NV:q")

    def test_no_room_for_the_line_not_sent_never_cut(self):
        got = self.post(channel="2", json={"content": "x" * 1990}, from_channel="1")
        self.assertFalse(got["ok"])
        self.assertIn("放不下來源那一行", got["why"])
        self.assertEqual(self.sent(), [])
        many = {"flags": 32768, "components": [{"type": 10, "content": str(i)} for i in range(40)]}
        got = self.post(channel="2", json=many, from_channel="1")
        self.assertIn("上限 40", got["why"])

    def test_the_rules_of_p1c_and_the_reserved_line(self):
        for body, words in (({"content": "@everyone"}, "@everyone"),
                            ({"content": "x", "components": [{"type": 1, "components": [
                                {"type": 2, "style": 1, "label": "a", "custom_id": "ask:1"}]}]}, "user:"),
                            ({"content": "-# 📨 來自 #秘書\n核准了"}, "機器人自己用的格式"),
                            ({"content": "📨 來自 #秘書"}, "機器人自己用的格式"), ([], "JSON 物件")):
            got = self.post(channel="2", json=body, from_channel="1")
            self.assertFalse(got["ok"], body)
            self.assertIn(words, got["why"], body)
        got = self.post(channel="2", json={"content": "<@7>"}, ping=["user:7"], from_channel="1")
        self.assertIn("json_mentions", got["why"])
        self.assertEqual(self.sent(), [])

    def test_mention_owner_pings_and_is_said_when_the_words_do(self):
        self.post(channel="2", json={"content": "<@42> look"}, ping=["owner"], from_channel="1")
        self.assertEqual(self.sent()[0][2]["allowed_mentions"], {"parse": [], "users": ["42"]})
        self.assertIn("用 --mention owner @ 了使用者", self.bot.audits[-1])

    def test_flags_reply_and_files(self):
        self.post(channel="2", json={"content": "表", "embeds": [{"image": {"url": "attachment://t.png"}}]},
                  from_channel="1", silent=True, no_embeds=True, reply_to="555", file_name="t.png",
                  file_b64=base64.b64encode(b"png").decode(), more_files=[{"name": "u.csv", "b64": "YQ=="}])
        (method, path, body, files), = self.sent()
        self.assertEqual(body["flags"], 4096 | 4)
        self.assertEqual(body["message_reference"], {"message_id": "555", "channel_id": "2", "fail_if_not_exists": False})
        self.assertEqual(body["attachments"], [{"id": 0, "filename": "t.png"}, {"id": 1, "filename": "u.csv"}])
        self.assertEqual(files, ["t.png", "u.csv"])

    def test_a_new_thread_opened_and_posted_in(self):
        got = self.post(channel="2", json={"content": "開頭"}, from_channel="1", thread_new="討論")
        self.assertEqual([c[:2] for c in self.sent()], [("POST", "/channels/2/threads"), ("POST", "/channels/700/messages")])
        self.assertEqual(self.sent()[0][2], {"name": "討論", "type": 11, "auto_archive_duration": 1440})
        self.assertEqual(got["channel_id"], "700")
        self.assertTrue(self.sent()[1][2]["content"].startswith("-# 📨 來自 <#1>"))
        self.assertIn("#lab／討論串 700", self.bot.audits[-1])
        got = self.post(channel="3", json={"content": "x"}, from_channel="1", thread_new="再一個")
        self.assertIn("不能開在討論串裡", got["why"])

    def test_edit_ones_own_with_its_line_put_back_never_anothers(self):
        self.post(channel="2", json={"content": "v1"}, from_channel="1")
        self.bot.http.calls[:] = []
        got = self.post(channel="2", json={"content": "v2"}, from_channel="1", edit_message="900")
        self.assertTrue(got["ok"], got)
        (method, path, body, files), = self.sent()
        self.assertEqual((method, path, body["content"]), ("PATCH", "/channels/2/messages/900", "-# 📨 來自 <#1>\nv2"))
        self.assertEqual(ledger.entry(900, 2)["from"], "channel:1", "an edit keeps its sender")
        got = self.post(channel="2", json={"content": "v3"}, from_channel="2", edit_message="900")
        self.assertIn("只能改自己發的", got["why"])
        got = self.post(channel="2", json={"content": "v3"}, from_channel="1", edit_message="901")
        self.assertIn("ledger", got["why"])

    def test_an_edit_never_leaves_it_without_where_it_comes_from(self):
        """Both reviews on ddfda347, manager 11-17: in another's channel an edit keeps its source: a relay card cannot
        be put back (refused, exit 4), nor a post of no source line there; dcctl edit refuses a card too."""
        ledger.note(2, 555, "post", frm="channel:1", framed=("card",))
        ledger.note(2, 556, "post", frm="channel:1", framed=())
        for mid, words in (("555", "轉傳卡片"), ("556", "沒有來源行")):
            got = self.post(channel="2", json={"content": "改掉了", "embeds": []}, from_channel="1", edit_message=mid)
            self.assertFalse(got["ok"], mid)
            self.assertIn(words, got["why"], mid)
        self.assertEqual(self.sent(), [])
        from bot import rules
        self.assertIn("轉傳卡片", rules.change_refused({"intent": "post", "from": "channel:1", "framed": ["card"]},
                                                        "channel:1"))
        ledger.note(1, 557, "post", frm="channel:1", framed=())  # (its own channel: plain, no line wanted)
        self.assertTrue(self.post(channel="1", json={"content": "x"}, from_channel="1", edit_message="557")["ok"])

    def test_dcctl_edit_puts_the_line_back_or_refuses(self):
        """#code-review on cd055017: dcctl edit of a post to another's channel (send-json is dc post --json) keeps its
        line on top; no room for it: refused, the message as it was."""
        self.post(channel="2", json={"content": "hello"}, from_channel="1")
        self.bot.http.calls[:] = []
        got = self.post(channel="2", raw={"content": "changed"}, edit_message="900", label="#lab", who="channel:1")
        self.assertTrue(got["ok"], got)
        self.assertEqual(self.sent()[0][1:3], ("/channels/2/messages/900", {"content": "-# 📨 來自 <#1>\nchanged"}))
        self.bot.http.calls[:] = []
        got = self.post(channel="2", raw={"content": "x" * 1995}, edit_message="900", label="#lab", who="channel:1")
        self.assertIn("放不下來源那一行", got["why"])
        self.assertEqual(self.sent(), [])

    def test_a_programs_edit_by_its_key_a_claudes_old_one_elsewhere_not(self):
        """manager 11-17: a program (the supervisor, scripts) never gets the line, so its edits in any channel go by its
        key alone; a Claude's post in another's channel from before P1d (no line) cannot be edited, dcctl edit or
        --json --edit."""
        ledger.note(2, 560, "post", frm="program:host-a:x", framed=())
        got = self.post(channel="2", raw={"content": "新的"}, edit_message="560", label="#lab", who="program:host-a:x")
        self.assertTrue(got["ok"], got)
        self.assertEqual(self.sent()[0][2], {"content": "新的"})
        ledger.note(2, 561, "post", frm="channel:1", framed=())
        got = self.post(channel="2", raw={"content": "x"}, edit_message="561", label="#lab", who="channel:1")
        self.assertIn("沒有來源行", got["why"])

    def test_an_edit_in_its_channels_sleeping_thread_is_its_own_home(self):
        """C-191: a thread gone to sleep (archived) is not in the cache; its channel's Claude editing its post there is
        in its own home (asked of Discord), not refused for want of a source line; no answer: the ID, refused as before."""
        sleeping = Replying(4, "asleep")
        sleeping.parent_id = 1
        asked = []

        async def fetch_channel(cid):
            asked.append(cid)
            if cid == 4:
                return sleeping
            raise discord.NotFound(types.SimpleNamespace(status=404, reason="Not Found"), {"code": 10003, "message": "x"})
        self.bot.fetch_channel = fetch_channel
        ledger.note(4, 570, "post", frm="channel:1", framed=())
        got = self.post(channel="4", raw={"content": "改"}, edit_message="570", label="#asleep", who="channel:1")
        self.assertTrue(got["ok"], got)
        self.assertEqual(self.sent()[0][1:3], ("/channels/4/messages/570", {"content": "改"}))
        self.assertIn(4, asked)
        ledger.note(5, 571, "post", frm="channel:1", framed=())
        got = self.post(channel="5", raw={"content": "改"}, edit_message="571", label="#gone", who="channel:1")
        self.assertIn("沒有來源行", got["why"])

    def asleep(self, cid=4):
        """A thread of st-1 gone to sleep: not in the cache, Discord still has it (C-191 live test 2, L3 11-17)."""
        thread = Replying(cid, "asleep")
        thread.parent_id = 1

        async def fetch_channel(c):
            if c == cid:
                return thread
            raise discord.NotFound(types.SimpleNamespace(status=404, reason="Not Found"), {"code": 10003, "message": "x"})
        self.bot.fetch_channel = fetch_channel
        self.bot.http.archived.add(str(cid))
        return thread

    def test_json_edit_by_a_sleeping_threads_id(self):
        """L3's case: altention dc post <thread id> --json - --edit <id>, the thread archived: found (asked of
        Discord), opened, edited; it said 「找不到頻道」."""
        self.asleep()
        ledger.note(4, 590, "post", frm="channel:1", framed=())
        got = self.post(channel="4", json={"content": "改"}, from_channel="1", edit_message="590")
        self.assertTrue(got["ok"], got)
        self.assertEqual([c[:2] for c in self.sent()], [("PATCH", "/channels/4/messages/590"), ("PATCH", "/channels/4"),
                                                        ("PATCH", "/channels/4/messages/590")])
        self.assertIn("[altention dc post --json] 討論串 <#4> 收起來了，先打開再改訊息 590", self.bot.audits)

    def test_a_plain_post_or_a_payload_to_a_sleeping_thread(self):
        thread = self.asleep()
        self.post(channel="4", text="醒醒", from_channel="1")
        self.assertTrue(any("醒醒" in t for t in thread.sent), thread.sent)
        got = self.post(channel="4", json={"content": "x"}, from_channel="1")
        self.assertTrue(got["ok"], got)
        got = self.post(channel="5", json={"content": "x"}, from_channel="1")
        self.assertIn("找不到頻道 5", got["why"], "not on Discord either: as before")

    def test_a_framed_board_in_a_sleeping_thread_is_opened_to_be_changed(self):
        thread = self.asleep()
        self.bot.http.archived.discard("4")
        self.bot.chans[4] = thread
        self.post(channel="4", board="本週", json=[{"content": "一"}], from_channel="1")
        del self.bot.chans[4]
        self.bot.http.archived.add("4")
        self.bot.http.calls[:] = []
        got = self.post(channel="4", board="本週", json=[{"content": "二"}], from_channel="1")
        self.assertTrue(got["ok"], got)
        self.assertIn(("PATCH", "/channels/4", {"archived": False}, []), self.sent())
        last = self.sent()[-1]
        self.assertEqual((last[0], last[1].startswith("/channels/4/messages/"), last[2].get("content")), ("PATCH", True, "二"),
                         "changed in place, not posted anew")
        self.assertNotIn("4", self.bot.http.archived)

    def test_a_text_board_in_a_sleeping_thread_is_opened_to_be_changed(self):
        thread = self.asleep()
        self.bot.http.archived.discard("4")
        self.bot.chans[4] = thread
        self.post(channel="4", board="本週", text="一", from_channel="1")
        del self.bot.chans[4]
        self.bot.http.archived.add("4")
        http, edited = self.bot.http, []

        class Partial(object):
            def __init__(self, mid):
                self.id = mid

            async def edit(self, content=None, **kw):
                if "4" in http.archived:
                    raise discord.HTTPException(*ARCHIVED_ANSWER)
                edited.append(content)
                return self
        thread.get_partial_message = Partial
        self.post(channel="4", board="本週", text="二", from_channel="1")  # (a text board's receipt: no new message)
        self.assertNotIn("4", http.archived, "opened")
        self.assertTrue(edited and "二" in edited[-1], edited)
        self.assertEqual(len(thread.sent), 1, "changed in place, not posted anew")
        self.assertIn("[altention dc post --board] 討論串 <#4> 收起來了，先打開再改訊息", self.bot.audits[-1])

    def test_a_text_boards_picture_is_sent_whole_again_once_the_thread_is_opened(self):
        """#code-review on 4d75df4a: the first try reads the picture to its end (aiohttp); sent again after the thread
        is opened, it must be read from the start, not go as 0 bytes."""
        import base64
        thread = self.asleep()
        self.bot.http.archived.discard("4")
        self.bot.chans[4] = thread
        self.post(channel="4", board="本週", text="一", from_channel="1")
        del self.bot.chans[4]
        self.bot.http.archived.add("4")
        http, read = self.bot.http, []

        class Partial(object):
            def __init__(self, mid):
                self.id = mid

            async def edit(self, content=None, attachments=(), **kw):
                read.append([f.fp.read() for f in attachments])  # (as the upload does)
                if "4" in http.archived:
                    raise discord.HTTPException(*ARCHIVED_ANSWER)
                return self
        thread.get_partial_message = Partial
        self.post(channel="4", board="本週", text="二", from_channel="1", file_b64=base64.b64encode(b"PNG-bytes").decode(),
                  file_name="chart.png")
        self.assertEqual(read, [[b"PNG-bytes"], [b"PNG-bytes"]], "whole both times")

    def test_an_edit_in_a_thread_gone_to_sleep_opens_it_first(self):
        """C-191 live test 6 (manager 11-17): Discord refuses an edit in an archived thread (50083); the bot opens the
        thread, edits, and leaves it open (as a new message there would); dcctl edit and --json --edit alike."""
        self.t.parent_id = 1
        ledger.note(3, 580, "post", frm="channel:1", framed=())
        self.bot.http.archived.add("3")
        got = self.post(channel="3", raw={"content": "改"}, edit_message="580", label="#a-thread", who="channel:1")
        self.assertTrue(got["ok"], got)
        self.assertEqual([c[:3] for c in self.sent()], [("PATCH", "/channels/3/messages/580", {"content": "改"}),
                                                        ("PATCH", "/channels/3", {"archived": False}),
                                                        ("PATCH", "/channels/3/messages/580", {"content": "改"})])
        self.assertNotIn("3", self.bot.http.archived, "left open")
        self.assertIn("[dcctl edit] 討論串 <#3> 收起來了，先打開再改訊息 580", self.bot.audits)
        self.bot.http.calls[:] = []
        self.bot.http.archived.add("3")
        got = self.post(channel="3", json={"content": "再改"}, from_channel="1", edit_message="580")
        self.assertTrue(got["ok"], got)
        self.assertEqual(self.sent()[1][:3], ("PATCH", "/channels/3", {"archived": False}))

    def test_a_thread_it_cannot_open_is_said_plainly(self):
        ledger.note(3, 581, "post", frm="channel:1", framed=())
        self.bot.http.archived.add("3")
        self.bot.http.locked.add("3")
        got = self.post(channel="3", raw={"content": "改"}, edit_message="581", label="#a-thread", who="channel:1")
        self.assertEqual(got["why"], "dcctl edit：沒有改：討論串收起來了，機器人打不開（403 50013），打開後再改")
        got = self.post(channel="3", json={"content": "改"}, from_channel="1", edit_message="581")
        self.assertTrue(got["why"].startswith("altention dc post --json：沒有改：討論串收起來了"), got["why"])

    def test_a_new_post_or_another_refusal_is_not_retried(self):
        self.bot.http.refuse = {"code": 50083, "message": "Thread is archived"}
        got = self.post(channel="2", json={"content": "x"}, from_channel="1")
        self.assertIn("Discord 沒收（400 50083）", got["why"], "not an edit: as before")
        self.bot.http.refuse = {"code": 50035, "message": "Invalid Form Body"}
        ledger.note(2, 582, "post", frm="channel:2", framed=())
        got = self.post(channel="2", raw={"content": "x"}, edit_message="582", label="#lab", who="channel:2")
        self.assertIn("Discord 沒收（400 50035）", got["why"])
        self.assertEqual([c for c in self.bot.http.calls if c[1].count("/") == 2], [], "no thread opened")

    def test_a_refusal_names_the_command_once(self):
        """C-191: the reason the bot gives names the command the sender ran, once (dcctl's queue() and altention dc
        post print it as it is): --json --edit's says altention dc post --json, not dcctl."""
        ledger.note(2, 572, "post", frm="channel:1", framed=())
        got = self.post(channel="2", json={"content": "x"}, from_channel="1", edit_message="572")
        self.assertTrue(got["why"].startswith("altention dc post --json：沒有改："), got["why"])
        got = self.post(channel="2", raw={"content": "x"}, edit_message="572", label="#lab", who="channel:1")
        self.assertTrue(got["why"].startswith("dcctl edit：沒有改："), got["why"])

    def test_a_thread_is_not_opened_for_a_post_refused(self):
        got = self.post(channel="2", json={"content": "x" * 1990}, from_channel="1", thread_new="t")
        self.assertIn("放不下", got["why"])
        self.assertEqual(self.sent(), [], "no empty thread left")

    def test_what_discord_refuses_goes_back(self):
        self.bot.http.refuse = {"code": 50035, "message": "Invalid Form Body"}
        got = self.post(channel="2", json={"content": "x"}, from_channel="1")
        self.assertFalse(got["ok"])
        self.assertIn("Discord 沒收（400 50035）", got["why"])


class Delete(Base):
    """altention dc post --delete (manager 11-17, the rules of bot/rules.py): a post of the sender's own, by the ledger."""
    def delete(self, cid, mid, sender="1", **kw):
        return self.post(channel=str(cid), delete_message=str(mid), why="寫錯了", from_channel=sender, wait=30, **kw)

    def test_ones_own_taken_away_and_said(self):
        ledger.note(1, 600, "post", frm="channel:1", framed=())  # (its own channel: no line wanted)
        got = self.delete(1, 600)
        self.assertEqual((got["ok"], got["text"]), (True, "訊息 600 刪掉了"))
        self.assertEqual(self.a.deleted, [600])
        self.assertIsNone(ledger.entry(600, 1), "its entry goes too")
        self.assertIn("[altention dc post --delete] #st-1 刪了訊息 600（#st-1（查證過） 發的；理由：寫錯了）", self.bot.audits,
                      "what it said cannot be read here: left out")

    def test_what_it_said_is_kept_in_audit(self):
        """manager 11-17: the other Claude may have read it; #audit says what it said (its first words, masked)."""
        ledger.note(1, 605, "post", frm="channel:1", framed=())
        words = "第一行 " + "很長的內容" * 20

        async def fetch_message(mid):
            return types.SimpleNamespace(content=words, embeds=[], components=[])
        self.a.fetch_message = fetch_message
        self.assertTrue(self.delete(1, 605)["ok"])
        line = self.bot.audits[-1]
        self.assertIn("刪了訊息 605（#st-1（查證過） 發的，內容：「第一行 很長的內容", line)
        self.assertIn("…」；理由：寫錯了）", line)
        self.assertEqual(len(line.split("「")[1].split("」")[0]), posts.Posts.DELETED_SHOWN + 1)
        ledger.note(1, 606, "post", frm="channel:1", framed=("card",))

        async def card(mid):
            return types.SimpleNamespace(content="", embeds=[discord.Embed(description="轉傳的話")], components=[])
        self.a.fetch_message = card
        self.assertTrue(self.delete(1, 606)["ok"])
        self.assertIn("內容：「", self.bot.audits[-1])
        self.assertIn("轉傳的話", self.bot.audits[-1], "a 📨 card's words")
        ledger.note(1, 607, "post", frm="channel:1", framed=())
        key = "sk-ant-" + "a" * 30  # (as tests/test_masking.py's: a made-up key of the shape masking knows)

        async def secret(mid):
            return types.SimpleNamespace(content="token " + key, embeds=[], components=[])
        self.a.fetch_message = secret
        self.assertTrue(self.delete(1, 607)["ok"])
        self.assertNotIn(key, self.bot.audits[-1], "masked before it goes to #audit (#code-review on 4dd7d92d)")
        self.assertIn("內容：「token [REDACTED]", self.bot.audits[-1])

    def test_in_anothers_channel_with_where_it_comes_from(self):
        ledger.note(2, 601, "post", frm="channel:1", framed=("source",))
        ledger.note(2, 602, "post", frm="channel:1", framed=("card",))  # (a 📨 relay card: the sender's own post)
        ledger.note(2, 603, "post", frm="channel:1", framed=())  # (a Claude's from before P1d: no line)
        ledger.note(2, 604, "post", frm="program:host-a:x", framed=())  # (a program's: never a line, by its key)
        self.assertTrue(self.delete(2, 601)["ok"])
        self.assertTrue(self.delete(2, 602)["ok"])
        got = self.delete(2, 603)
        self.assertFalse(got["ok"])
        self.assertIn("沒有來源行", got["text"])
        got = self.post(channel="2", delete_message="604", why="w", from_name="host-a 上的程式（x）", from_host="host-a",
                        from_machine="host-a", from_folder="x", verified=False, wait=30)
        self.assertTrue(got["ok"], got)
        self.assertEqual(self.b.deleted, [601, 602, 604])

    def test_never_anothers_a_card_a_board_or_a_reply(self):
        ledger.note(1, 610, "post", frm="channel:2", framed=())
        ledger.note(1, 611, "card", frm=None)
        ledger.note(1, 612, "board", frm=None)
        ledger.note(1, 613, "reply", frm=None)
        for mid, words in ((610, "只能刪自己發的"), (611, "機器人的卡片"), (612, "看板"), (613, "不是用工具發的訊息"),
                           (699, "紀錄（ledger）裡沒有這則")):
            got = self.delete(1, mid)
            self.assertFalse(got["ok"], mid)
            self.assertIn(words, got["text"], mid)
            self.assertTrue(got["text"].startswith("altention dc post --delete：沒有刪："), got["text"])
        self.assertFalse(hasattr(self.a, "deleted"), "nothing taken away")
        self.assertIn("[altention dc post --delete] #st-1：altention dc post --delete：沒有刪：", self.bot.audits[-1])

    def test_one_gone_already_or_refused_by_discord(self):
        ledger.note(1, 620, "post", frm="channel:1", framed=())
        ledger.note(1, 621, "post", frm="channel:1", framed=())

        def partial(mid):
            class Gone(object):
                id = mid

                async def delete(self):
                    if mid == 620:
                        raise discord.NotFound(types.SimpleNamespace(status=404, reason="x"), {"code": 10008, "message": "Unknown Message"})
                    raise discord.Forbidden(types.SimpleNamespace(status=403, reason="x"), {"code": 50013, "message": "Missing Permissions"})
            return Gone()
        self.a.get_partial_message = partial
        got = self.delete(1, 620)
        self.assertEqual((got["ok"], got["text"]), (True, "訊息 620 已經不在了（之前就刪掉了）"))
        got = self.delete(1, 621)
        self.assertFalse(got["ok"])
        self.assertIn("Discord 沒收（403 50013）", got["text"])
        self.assertIsNotNone(ledger.entry(621, 1), "not taken away: its entry stays")


class Board(Base):
    """--board --json: a framed board, a payload a message, kept up to date in place (manager 11-16 design, P1d)."""
    def board(self, parts, **kw):
        return self.post(channel="2", board="本週", json=parts, from_channel="1", **kw)

    def test_posted_then_changed_in_place_fewer_deleted(self):
        got = self.board([V2, {"content": "第二則"}])
        self.assertTrue(got["ok"], got)
        self.assertEqual([(c[0], c[1], c[2].get("flags")) for c in self.sent()],
                         [("POST", "/channels/2/messages", 32768 | 4096), ("POST", "/channels/2/messages", 4096)])
        self.assertNotIn("-# 📨", str(self.sent()), "a board: no line of where it comes from")
        self.assertEqual(self.bot.channels_state["2"]["boards"]["本週"], [900, 901])
        self.assertEqual((ledger.entry(900, 2)["intent"], ledger.entry(900, 2)["key"]), ("board", "本週"))
        self.bot.http.calls[:] = []
        self.board(V2)
        self.assertEqual([c[:2] for c in self.sent()], [("PATCH", "/channels/2/messages/900")])
        self.assertEqual(self.b.deleted, [901])
        self.assertEqual(self.bot.channels_state["2"]["boards"]["本週"], [900])
        self.assertIn("[dc post --board] #lab 的看板「本週」：1 則", self.bot.audits[-1])

    def test_a_text_board_framed_in_place_framed_back_to_plain_anew_with_its_pin(self):
        self.bot.channels_state["2"]["boards"] = {"本週": [500]}  # (a text board from before)
        self.board(V2)
        self.assertEqual([c[:2] for c in self.sent()], [("PATCH", "/channels/2/messages/500")])
        self.bot.http.calls[:] = []
        self.bot.http.pins.add("500")
        self.board({"content": "純文字"})
        self.assertEqual([c[:2] for c in self.sent()], [("GET", "/channels/2/messages/500"), ("POST", "/channels/2/messages"),
                                                        ("PUT", "/channels/2/messages/pins/900")])
        self.assertEqual(self.b.deleted, [500])
        self.assertEqual(self.bot.channels_state["2"]["boards"]["本週"], [900])

    def test_one_gone_is_posted_again(self):
        self.bot.channels_state["2"]["boards"] = {"本週": [500]}
        self.bot.http.gone.add("500")
        self.board({"content": "x"})
        self.assertEqual([c[:2] for c in self.sent()], [("PATCH", "/channels/2/messages/500"), ("POST", "/channels/2/messages")])
        self.assertEqual(self.bot.channels_state["2"]["boards"]["本週"], [900])

    def test_one_not_posted_again_keeps_the_rest_on_record(self):
        """Both reviews on ddfda347: the message after the one Discord refused stay on record (changed or deleted
        next time), never left in the channel out of sight."""
        self.bot.channels_state["2"]["boards"] = {"本週": [500, 501, 502]}
        self.bot.http.gone.add("500")
        self.bot.http.refuse_posts = 500
        got = self.board([{"content": "a"}, {"content": "b"}])
        self.assertFalse(got["ok"])
        self.assertEqual(self.bot.channels_state["2"]["boards"]["本週"], [501, 502])
        self.assertEqual(getattr(self.b, "deleted", []), [], "502 not deleted either: next time")
        self.assertEqual(self.bot.channels_state["2"]["board_fails"]["本週"], 1)

    def test_failing_again_and_again_is_said_once(self):
        self.bot.channels_state["2"]["boards"] = {"本週": [500]}
        self.bot.http.gone.add("500")
        self.bot.http.refuse_posts = 500
        notes = []

        async def notify(post, _):
            notes.append(post["text"])
        self.p.notify = notify
        for _ in range(posts.BOARD_FAILS + 1):
            self.board({"content": "a"})
        self.assertEqual(len(notes), 1)
        self.assertIn("連續 %d 次更新失敗" % posts.BOARD_FAILS, notes[0])

    def test_back_to_a_text_board_framed_ones_posted_anew_with_their_pin(self):
        self.board([V2, V2])
        self.bot.http.pins.add("900")
        self.bot.http.calls[:] = []
        asyncio.run(self.p.board(self.b, "本週", "文字看板"))
        self.assertEqual(sorted(self.b.deleted), [900, 901])
        self.assertEqual([c[:2] for c in self.sent()], [("GET", "/channels/2/messages/900"),
                                                        ("PUT", "/channels/2/messages/pins/1")],
                         "its pin moved; the second, one part too many, deleted as ever")
        self.assertEqual(len(self.b.sent), 1, "the text posted anew")
        self.assertNotIn("本週", self.bot.channels_state["2"].get("boards_v2", {}))

    def test_a_text_failing_and_a_frame_not_deleted_each_kept_in_its_place(self):
        """#code-review-2 on cd055017: a text part that cannot be changed this time and a framed one that cannot be
        deleted, in one round: which of them is framed stays in its own place."""
        class Stuck(object):
            def __init__(self, mid):
                self.id = mid

            async def edit(self, **kw):
                raise OSError("503")

            async def delete(self):
                raise OSError("503")
        self.b.get_partial_message = Stuck
        self.bot.channels_state["2"]["boards"] = {"本週": [500, 501, 502]}
        self.bot.channels_state["2"]["boards_v2"] = {"本週": [False, True, False]}
        asyncio.run(self.p.board(self.b, "本週", "a" * 1990))  # (three parts, as split_message cuts it)
        self.assertEqual(self.bot.channels_state["2"]["boards"]["本週"], [500, 501, 502])
        self.assertEqual(self.bot.channels_state["2"]["boards_v2"]["本週"], [False, True, False])

    def test_the_rules_on_each_part(self):
        bad = {"components": [{"type": 1, "components": [{"type": 2, "style": 1, "label": "a", "custom_id": "x"}]}]}
        got = self.board([{"content": "ok"}, bad])
        self.assertIn("user:", got["why"])
        self.assertEqual(self.sent(), [])


class PlainReplyAndSilent(Base):
    def test_a_plain_post_answers_a_message_and_goes_silently(self):
        self.post(channel="2", text="報告", from_channel="1", reply_to="555", silent=True)
        (ref, silent), = self.b.refs
        self.assertEqual((ref.message_id, ref.channel_id, ref.fail_if_not_exists, silent), (555, 2, False, True))
        self.post(channel="1", text="自己的", from_channel="1", reply_to="556")
        (ref, silent), = self.a.refs
        self.assertEqual((ref.message_id, silent), (556, None))


class Payloads(unittest.TestCase):
    def test_the_pieces(self):
        self.assertEqual(payloads.sourced({"content": ""}, "<#1>")[0]["content"], "-# 📨 來自 <#1>")
        self.assertEqual(payloads.sourced({"content": "```py\nx\n```"}, "<#1>")[0]["content"], "-# 📨 來自 <#1>\n```py\nx\n```")
        body = {"content": "a"}
        payloads.sourced(body, "<#1>")
        self.assertEqual(body, {"content": "a"}, "never changed in place")
        self.assertEqual(payloads.count([{"type": 17, "components": [{"type": 9, "components": [{"type": 10}],
                                                                        "accessory": {"type": 2}}]}]), 4)
        self.assertEqual(payloads.flagged({"flags": 32768}, silent=True), {"flags": 32768 | 4096})
        self.assertEqual(payloads.with_files({"attachments": []}, ["a"]), {"attachments": []}, "its own kept")

    def test_reserved_after_small_print_too(self):
        for text in ("-# 📨 來自 #x", "📨 來自 #x", "-# 🔔 **[x]** y", "-# 📌 **板**\n"):
            self.assertTrue(frames.reserved(text), text)
        self.assertIsNone(frames.reserved("-# 小字"))
        self.assertEqual(frames.of_guessed("-# 📨 來自 <#5>\n內容", by_bot=True),
                         {"kind": frames.RELAYED, "sender": "<#5>", "text": "內容"}, "no ledger: by its look")


if __name__ == "__main__":
    unittest.main()
