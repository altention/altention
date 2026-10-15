"""What a forwarded message brings to the channel's agent (B-13, stage 3b): who said the original (the channel's
agent, the owner, another conversation, a notification, a board, the bot), when, its id and link, and its content
without the frame the bot put round it; what the owner types after it is marked as the owner's. Discord's forward
carries a snapshot with no author: the original is read once, in the message's own handling, five seconds at most."""
import asyncio
import datetime
import tempfile
import time
import unittest
from unittest import mock

import discord

from bot import config, frames, util
from tests import tmuxguard  # noqa: F401,E402  (no test reaches a real tmux server)

BOT, OWNER, WHEN = 900, 111, datetime.datetime(2026, 10, 13, 7, 28, 13, tzinfo=datetime.timezone.utc)
HEAD = util.FORWARDED + "\n"


class Author(object):
    def __init__(self, uid, name="someone"):
        self.id, self.display_name, self.name, self.bot = uid, name, name, uid == BOT


class Original(object):
    def __init__(self, author, reference=None):
        self.author, self.reference = author, reference


class Channel(object):
    def __init__(self, cid, name, original=None, error=None, parent=None, slow=0):
        self.id, self.name, self.original, self.error, self.slow = cid, name, original, error, slow
        self.parent, self.parent_id, self.asked = parent, getattr(parent, "id", None), []

    async def fetch_message(self, mid):
        self.asked.append(mid)
        if self.slow:
            await asyncio.sleep(self.slow)
        if self.error:
            raise self.error
        return self.original


class Embed(object):
    def __init__(self, author=None, description=None, title=None, url=None):
        self.author, self.description, self.title, self.url = type("A", (), {"name": author})(), description, title, url


class Snap(object):
    def __init__(self, content="", embeds=(), attachments=(), stickers=(), edited=None):
        self.content, self.embeds, self.attachments, self.stickers = content, list(embeds), list(attachments), list(stickers)
        self.created_at, self.edited_at = WHEN, edited


class Here(object):
    id, name = 50, "here"


def forward(snaps, channel_id=77, message_id=5, guild_id=1, mid=1, text=""):
    ref = type("Ref", (), {"channel_id": channel_id, "message_id": message_id, "guild_id": guild_id,
                           "jump_url": "https://discord.com/channels/%s/%s/%s" % (guild_id, channel_id, message_id),
                           "type": discord.MessageReferenceType.forward})()
    return type("Msg", (), {"content": text, "message_snapshots": list(snaps), "channel": Here(), "id": mid, "attachments": [],
                            "reference": ref, "guild": type("G", (), {"id": 1})()})()


def http_error(cls, status):
    return cls(type("R", (), {"status": status, "reason": "x"})(), "x")


class Forwarded(unittest.TestCase):
    def setUp(self):
        state, owner = config.STATE, config.OWNER_ID
        self.addCleanup(lambda: (setattr(config, "STATE", state), setattr(config, "OWNER_ID", owner)))
        config.STATE, config.OWNER_ID = tempfile.mkdtemp(), OWNER
        from bot import main as botmain
        with mock.patch.object(botmain.Bot, "save", lambda self: None):
            self.bot = botmain.Bot()
        self.channels = {}
        self.bot.get_channel = lambda cid: self.channels.get(cid)
        user = mock.patch.object(type(self.bot), "user", new_callable=mock.PropertyMock, return_value=Author(BOT, "bot"))
        user.start()
        self.addCleanup(user.stop)
        self.when = WHEN.astimezone().strftime("%Y-%m-%d %H:%M:%S")

    def channel(self, original=None, **kw):
        ch = self.channels[77] = Channel(77, "秘書", original, **kw)
        return ch

    def text(self, snaps, **kw):
        return asyncio.run(self.bot.forwarded(forward(snaps, **kw)))

    def line(self, who, mid=5):
        return "[%s] %s (id %s)\n連結：https://discord.com/channels/1/77/%s\n" % (self.when, who, mid, mid)

    def test_the_channels_agents_reply_without_the_status_line(self):
        self.channel(Original(Author(BOT)))
        self.bot.channels_state["77"] = {"turns": {"5": ["/p", 0, None, None]}}
        got = self.text([Snap("改好了。\n第二行\n-# Bash×1 · 4 秒 · context 5%")])
        self.assertEqual(got, HEAD + self.line("#秘書 的 agent 的回覆") + "> 改好了。\n> 第二行\n")

    def test_each_kind_of_thing_the_bot_posts(self):
        self.channel(Original(Author(BOT)))
        for snap, who, quoted in (
                (Snap(">>> 我先看一下"), "#秘書 的 agent 中途說的話", "> 我先看一下\n"),
                (Snap("", [Embed("📨 來自 #bot-dev", "請看這個\n第二行")]), "#bot-dev 傳到 #秘書 的訊息", "> 請看這個\n> 第二行\n"),
                (Snap("", [Embed("📨 來自 lap 上的 Claude（proj）（2/3）", "中間那張")]), "lap 上的 Claude（proj） 傳到 #秘書 的訊息（第 2/3 張）", "> 中間那張\n"),
                (Snap("🔔 **[lap 上的 backup]** 備份完成"), "lap 上的 backup 在 #秘書 的通知", "> 備份完成\n"),
                (Snap("📌 **進度**\n-# 更新於 10-13 15:00\n一、做完了"), "#秘書 的看板「進度」（更新於 10-13 15:00）", "> 一、做完了\n"),
                (Snap("⌨️ **終端機那邊輸入**\n> ls -l\n> 第二行"), "使用者在 #秘書 的終端機打的", "> ls -l\n> 第二行\n"),
                (Snap("ℹ️ 這個頻道的 Claude 結束了"), "機器人在 #秘書 的通知", "> ℹ️ 這個頻道的 Claude 結束了\n"),
                (Snap("🧑 **你**\nhi\n💬 hello"), "#秘書 的 📋 過程（機器人整理的）", "> 🧑 **你**\n> hi\n> 💬 hello\n"),
                (Snap("很久以前的回覆\n-# 3 秒 · context 9%"), "機器人在 #秘書 的訊息（分不出是回覆還是通知）", "> 很久以前的回覆\n")):
            self.assertEqual(self.text([snap]), HEAD + self.line(who) + quoted, who)

    def test_the_owners_own_words_and_somebody_elses_keep_what_looks_like_a_frame(self):
        self.channel(Original(Author(OWNER)))
        self.assertEqual(self.text([Snap(">>> 我自己打的\n-# 小字")]), HEAD + self.line("使用者（在 #秘書）") + "> >>> 我自己打的\n> -# 小字\n")
        self.channel(Original(Author(7, "阿明")))
        self.assertEqual(self.text([Snap("hi")]), HEAD + self.line("#秘書 裡 阿明 的訊息") + "> hi\n")

    def test_when_the_original_cannot_be_read(self):
        for error, why in ((http_error(discord.NotFound, 404), "原訊息或它的頻道已經不在"), (http_error(discord.Forbidden, 403), "機器人讀不到那個頻道"),
                           (OSError("x"), "讀不到（OSError）")):
            self.channel(error=error)
            self.assertEqual(self.text([Snap("還在的內容\n-# 3 秒")]), HEAD + self.line("#秘書 的訊息（查不到是誰說的：%s）" % why) + "> 還在的內容\n> -# 3 秒\n",
                             "the snapshot's text as it is: nothing is taken for a frame when the author is not known")
        self.channels.clear()

        async def gone(cid):
            raise http_error(discord.NotFound, 404)
        self.bot.fetch_channel = gone
        self.assertEqual(self.text([Snap("x")]), HEAD + self.line("別的頻道 的訊息（查不到是誰說的：原訊息或它的頻道已經不在）") + "> x\n")
        got = self.text([Snap("", [Embed("📨 來自 #a", "卡片的字")])], guild_id=2)
        self.assertEqual(got, HEAD + "[%s] 別的伺服器 的訊息（查不到是誰說的：在別的伺服器，機器人讀不到） (id 5)\n連結：https://discord.com/channels/2/77/5\n> 卡片的字\n" % self.when)

    def test_the_lookup_waits_five_seconds_at_most_and_only_in_this_messages_own_handling(self):
        ch = self.channel(Original(Author(OWNER)), slow=30)
        self.bot.FORWARD_LOOKUP = 0.05
        t0 = time.time()
        got = self.text([Snap("x")])
        self.assertLess(time.time() - t0, 1)
        self.assertEqual(got, HEAD + self.line("#秘書 的訊息（查不到是誰說的：Discord 沒有回應）") + "> x\n")
        self.assertEqual(ch.asked, [5], "asked once")
        self.assertEqual(type(self.bot).FORWARD_LOOKUP, 5)

    def test_what_else_the_original_held(self):
        self.channel(Original(Author(OWNER), reference=type("R", (), {"message_id": 4, "type": discord.MessageReferenceType.default})()))
        got = self.text([Snap("看這個", [Embed(None, "預覽的說明" * 100, "標題", "https://example.org/a")], stickers=[type("S", (), {"name": "貓"})()],
                              edited=WHEN)])
        self.assertEqual(got, HEAD + self.line("使用者（在 #秘書）") + "> 看這個\n（原訊息是回覆 id 4 的那一則）\n（原訊息編輯過）\n（embed：標題｜%s｜https://example.org/a）\n（貼圖：貓）\n"
                         % ("預覽的說明" * 100)[:300])

    def test_attachments_are_saved_as_before(self):
        self.channel(Original(Author(OWNER)))

        async def save(shim):
            return ["[附件] a.png 已存到 /x/a.png（用 Read 工具開啟；圖片也可以直接看）"] if shim.attachments else []
        from bot import attachments
        with mock.patch.object(attachments, "save", save):
            got = self.text([Snap("圖", attachments=[object()])])
        self.assertEqual(got, HEAD + self.line("使用者（在 #秘書）") + "> 圖\n[附件] a.png 已存到 /x/a.png（用 Read 工具開啟；圖片也可以直接看）\n")

    def test_a_message_of_a_thread(self):
        parent = Channel(70, "秘書")
        self.channels[77] = Channel(77, "📋 整理 notes", Original(Author(BOT)), parent=parent)
        self.bot.channels_state["70"] = {"turns": {"9": ["/p", 0, None, None]}}  # (the parent remembers its own replies: none of them is this message)
        self.assertEqual(self.text([Snap("🔧 **Bash**\n```\nls\n```")]),
                         HEAD + self.line("#秘書 的討論串「📋 整理 notes」 的 📋 過程（機器人整理的）") + "> 🔧 **Bash**\n> ```\n> ls\n> ```\n")
        self.channels.clear()

        async def asleep(cid):
            return Channel(77, "睡著的串", Original(Author(OWNER)), parent=parent)
        self.bot.fetch_channel = asleep
        self.assertEqual(self.text([Snap("x")]), HEAD + self.line("使用者（在 #秘書 的討論串「睡著的串」）") + "> x\n", "a thread gone to sleep is fetched")

    def test_several_forwards_and_what_the_owner_says_after(self):
        """Each forwarded message has its own lines; what the owner types after them is marked as the owner's."""
        self.channel(Original(Author(OWNER)))
        got = []

        async def deliver(message, text, now=False):
            got.append(text)

        async def passed(message, text):
            return text
        self.bot.deliver, self.bot.with_reply, self.bot.FORWARD_WAIT = deliver, passed, 0.2

        async def go():
            first = await self.bot.forwarded(forward([Snap("第一則")], message_id=5, mid=1))
            second = await self.bot.forwarded(forward([Snap("第二則")], message_id=6, mid=2))
            self.bot.hold_forward(forward([Snap("第一則")], mid=1), first)
            self.bot.hold_forward(forward([Snap("第二則")], mid=2), second)
            held = self.bot.forwards.pop(50)
            held[2].cancel()
            return await self.bot.held_text(held[1])
        both = asyncio.run(go())
        self.assertEqual(both, HEAD + self.line("使用者（在 #秘書）") + "> 第一則\n" + HEAD + self.line("使用者（在 #秘書）", 6) + "> 第二則\n")
        self.assertEqual(util.THEN, "（使用者接著說：）")
        import inspect
        from bot import main as botmain
        self.assertIn('(util.THEN + "\\n" if forwarded and text else "")', inspect.getsource(botmain.Bot.on_message))


class InOrder(unittest.TestCase):
    """A forward and what the owner types right after it are one prompt, the forward first, however long the
    original takes to be read (#code-review of 6d02f07: the comment went ahead and alone when it came while the
    original was still being read). The bot's real on_message."""

    def setUp(self):
        state, owner = config.STATE, config.OWNER_ID
        self.addCleanup(lambda: (setattr(config, "STATE", state), setattr(config, "OWNER_ID", owner)))
        config.STATE, config.OWNER_ID = tempfile.mkdtemp(), OWNER
        from bot import main as botmain
        with mock.patch.object(botmain.Bot, "save", lambda self: None):
            self.bot = botmain.Bot()
        user = mock.patch.object(type(self.bot), "user", new_callable=mock.PropertyMock, return_value=Author(BOT, "bot"))
        user.start()
        self.addCleanup(user.stop)
        self.got = []

        async def deliver(message, text, now=False):
            self.got.append(text)

        async def passed(message, text):
            return text
        self.bot.deliver, self.bot.with_reply, self.bot.FORWARD_WAIT = deliver, passed, 0.25
        self.n = 100

    def message(self, text="", snap=None, message_id=5):
        self.n += 1
        m = forward([snap] if snap else [], message_id=message_id, mid=self.n, text=text)
        m.author, m.mentions = Author(OWNER), []
        if not snap:
            m.reference = None
        return m

    def run_(self, steps, lookup, wait=0.25):
        """`steps`: [(seconds after the start, a message)]; `lookup`: how long the original takes to be read; `wait`:
        how long a forward waits for a comment (three seconds in the bot)."""
        self.bot.FORWARD_WAIT = wait
        self.bot.get_channel = lambda cid: Channel(77, "秘書", Original(Author(OWNER)), slow=lookup) if cid == 77 else None

        async def go():
            async def at(delay, m):
                await asyncio.sleep(delay)
                await self.bot.on_message(m)
            await asyncio.gather(*(at(d, m) for d, m in steps))
            await asyncio.sleep(self.bot.FORWARD_WAIT + min(lookup, self.bot.FORWARD_LOOKUP) + 0.15)
        del self.got[:]
        asyncio.run(go())
        return list(self.got)

    def test_however_long_the_original_takes_and_whenever_the_comment_comes(self):
        # (the reviewer's twelve: the original read in 0, 0.15, 0.3, 1.0 s; the comment 0.02, 0.1, 0.5 s after the forward;
        # the bot's wait of 3 s. Here all of it a fifth as long.)
        for lookup in (0, 0.03, 0.06, 0.2):
            for after in (0.004, 0.02, 0.1):
                got = self.run_([(0, self.message(snap=Snap("原訊息"))), (after, self.message("請照這個改"))], lookup, wait=0.6)
                self.assertEqual(len(got), 1, (lookup, after, got))
                self.assertTrue(got[0].startswith(HEAD) and got[0].endswith("> 原訊息\n" + util.THEN + "\n請照這個改"), (lookup, after, got[0][-60:]))
                self.assertIn("使用者（在 #秘書）", got[0], "and the original was read: who said it is there")

    def test_a_forward_alone_goes_when_its_original_is_read_even_after_the_wait(self):
        got = self.run_([(0, self.message(snap=Snap("原訊息")))], 0.4, wait=0.1)  # (the reading takes longer than the wait)
        self.assertEqual(len(got), 1)
        self.assertTrue(got[0].endswith("> 原訊息") and "使用者（在 #秘書）" in got[0], got[0][-80:])
        self.bot.FORWARD_LOOKUP = 0.2
        got = self.run_([(0, self.message(snap=Snap("原訊息")))], 5, wait=0.1)
        self.assertEqual(len(got), 1, "the limit of the reading: it goes with what there is")
        self.assertIn("查不到是誰說的：Discord 沒有回應", got[0])

    def test_several_forwards_and_words_between_them(self):
        f1, f2, f3 = (self.message(snap=Snap("第%d則" % n), message_id=n) for n in (1, 2, 3))
        got = self.run_([(0, f1), (0.02, f2), (0.05, self.message("這兩則一起看")), (0.5, f3), (0.53, self.message("這是另一件"))], 0.1, wait=0.25)
        self.assertEqual(len(got), 2, got)
        self.assertEqual([got[0].index(x) < got[0].index(y) for x, y in (("> 第1則", "> 第2則"), ("> 第2則", util.THEN), (util.THEN, "這兩則一起看"))], [True] * 3)
        self.assertNotIn("第3則", got[0])
        self.assertTrue(got[1].endswith("> 第3則\n" + util.THEN + "\n這是另一件"), got[1][-60:])
        self.assertEqual(got[0].count(util.FORWARDED), 2, "each forward has its own lines")

    def test_a_comment_that_comes_while_the_lone_forward_is_about_to_go(self):
        """The wait is over and the forward waits only for its original: the comment still takes it along."""
        got = self.run_([(0, self.message(snap=Snap("原訊息"))), (0.2, self.message("晚了一點的話"))], 0.4, wait=0.1)
        self.assertEqual(len(got), 1, got)
        self.assertTrue(got[0].endswith("> 原訊息\n" + util.THEN + "\n晚了一點的話"))

    def test_a_second_message_right_after_does_not_overtake(self):
        """Two messages typed while the original is still being read: the forward with the first, then the second
        (#code-review: the second went ahead of both)."""
        got = self.run_([(0, self.message(snap=Snap("原訊息"))), (0.02, self.message("第一句")), (0.04, self.message("第二句"))], 0.3, wait=0.5)
        self.assertEqual(len(got), 2, got)
        self.assertTrue(got[0].endswith("> 原訊息\n" + util.THEN + "\n第一句"), got[0][-40:])
        self.assertEqual(got[1], "第二句")
        self.assertEqual(self.bot.merging, {})

    def test_a_forward_is_never_lost_to_an_error_in_reading_it(self):
        """What Discord hands over was written from the library's classes: should a field be other than thought, the
        forward still goes, with what the snapshot surely holds, and says the rest was not found out (#code-review:
        it went as an empty prompt, or not at all). Never an empty prompt, never the forward dropped."""
        plain = HEAD + "轉發的訊息\n> %s\n（查不到其他資訊：內部錯誤，已記下）"
        no_time, no_text, odd_embed = Snap("還是要送到的內容"), Snap(None), Snap("有怪 embed 的")
        no_time.created_at, no_text.content, odd_embed.embeds = "not a time", 12345, 7
        with mock.patch("traceback.print_exc") as said:
            self.assertEqual(self.run_([(0, self.message(snap=no_time))], 0), [plain % "還是要送到的內容"])
            self.assertEqual(self.run_([(0, self.message(snap=no_time)), (0.05, self.message("請看"))], 0), [plain % "還是要送到的內容" + "\n" + util.THEN + "\n請看"])
            self.assertEqual(self.run_([(0, self.message(snap=odd_embed))], 0), [plain % "有怪 embed 的"])
            got = self.run_([(0, self.message(snap=no_text))], 0)
            self.assertTrue(len(got) == 1 and got[0].startswith(HEAD) and "> （沒有文字）" in got[0], got)
            self.assertGreaterEqual(said.call_count, 3, "written to the log each time")

    def test_a_forward_with_no_reference_and_a_message_with_an_empty_list_of_snapshots(self):
        m = self.message(snap=Snap("沒有來源的轉發"))
        m.reference = None
        got = self.run_([(0, m)], 0)
        self.assertEqual(got, [HEAD + "[%s] 別的頻道 的訊息（查不到是誰說的：沒有來源）\n> 沒有來源的轉發" % WHEN.astimezone().strftime("%Y-%m-%d %H:%M:%S")])
        empty = self.message("只有一句話")
        empty.message_snapshots = []
        self.assertEqual(self.run_([(0, empty)], 0), ["只有一句話"], "no snapshot: not a forward")
        nothing = self.message("")
        nothing.message_snapshots = []
        self.assertEqual(self.run_([(0, nothing)], 0), [], "and nothing at all is nothing")

    def test_nothing_at_all_is_not_handed_over(self):
        async def nothing(message):
            return ""
        with mock.patch.object(self.bot, "forwarded", nothing):
            self.assertEqual(self.run_([(0, self.message(snap=Snap("x")))], 0), [])

    def test_words_with_no_forward_go_as_ever(self):
        self.assertEqual(self.run_([(0, self.message("只是一句話"))], 0), ["只是一句話"])


class Frames(unittest.TestCase):
    """bot/frames.py by itself: pure, a structure out, the words another layer's."""

    def test_the_structure(self):
        f = frames
        self.assertEqual(f.of("a\n-# Bash×1 · 3 秒 · context 5%", by_bot=True, remembered=True), {"kind": f.REPLY, "text": "a"})
        self.assertEqual(f.of("結論\n-# 註：這是 agent 自己寫的小字\n-# Bash×1 · 3 秒 · context 5%", by_bot=True, remembered=True),
                         {"kind": f.REPLY, "text": "結論\n-# 註：這是 agent 自己寫的小字"}, "only the bot's own status line goes")
        self.assertEqual(f.of("結論\n-# 註：小字", by_bot=True, remembered=True)["text"], "結論\n-# 註：小字")
        for line in ("-# 3 秒", "-# Read×2, Bash×1 · 12 秒 · 背景工作 1 個進行中 · context 41%", "-# context 7%", "-# 95 秒 · context 5% · <@42>"):
            self.assertEqual(f.unfooted("x\n" + line), "x", line)
        for line in ("-# 註：三秒後再看", "-# 見上", "- # 3 秒", "3 秒"):
            self.assertEqual(f.unfooted("x\n" + line), "x\n" + line, line)
        self.assertEqual(f.of("", [{"author": "📨 來自 #a（1/2）", "description": "d"}], by_bot=True), {"kind": f.RELAYED, "sender": "#a", "text": "d", "part": (1, 2)})
        self.assertEqual(f.of("📨 **來自 #a**\nold style", by_bot=True), {"kind": f.RELAYED, "sender": "#a", "text": "old style"})
        self.assertEqual(f.of("🔔 **[x 上的 y]** t", by_bot=True), {"kind": f.NOTICE, "sender": "x 上的 y", "text": "t"})
        self.assertEqual(f.of("📌 **b**\nno stamp", by_bot=True), {"kind": f.BOARD, "name": "b", "updated": None, "text": "no stamp"})
        self.assertEqual(f.of(">>> a", by_owner=True), {"kind": f.OWNER, "text": ">>> a"}, "only the bot's own messages have frames")
        self.assertEqual(f.of("x", author="n"), {"kind": f.OTHER, "sender": "n", "text": "x"})
        self.assertEqual(f.of(None, by_bot=True), {"kind": f.BOT_UNKNOWN, "text": ""})
        self.assertEqual(f.of("-# 3 秒 · context 5%", by_bot=True, remembered=True), {"kind": f.REPLY, "text": ""})

    def test_a_remembered_reply_is_a_reply_whatever_it_begins_with(self):
        """What the bot has on record is a plain fact, and goes ahead of how the message looks (#code-review)."""
        f = frames
        for content in ("📌 **重點**\n一、…\n-# 3 秒 · context 5%", "🔔 **[注意]** 這件事", ">>> 這是回覆裡的引用", "⌨️ **終端機那邊輸入**\n> x", "📨 **來自 #a**\nx"):
            got = f.of(content, by_bot=True, remembered=True)
            self.assertEqual((got["kind"], got["text"]), (f.REPLY, f.unfooted(content)), content)
        self.assertEqual(f.of("📌 **重點**\n一、…\n-# 3 秒 · context 5%", by_bot=True, remembered=True)["text"], "📌 **重點**\n一、…", "its first line stays")
        self.assertEqual(f.of("", [{"author": "📨 來自 #a", "description": "d"}], by_bot=True, remembered=True)["kind"], f.REPLY)

    def test_a_reply_no_longer_remembered_is_not_called_a_notice(self):
        """Only the newest replies are on record. One that is not, and begins like a notice of the bot's, has the
        status line of a reply under it: a reply or a notice, not to be told; never "the bot's notice"."""
        f = frames
        for begin in ("✅ 做完了", "⚠️ 注意這個", "📋 清單如下", "📝 筆記"):
            self.assertEqual(f.of(begin + "\n細節\n-# Bash×1 · 3 秒 · context 5%", by_bot=True), {"kind": f.BOT_UNKNOWN, "text": begin + "\n細節"}, begin)
            self.assertEqual(f.of(begin, by_bot=True)["kind"], f.BOT, "with no status line under it: a notice")
        self.assertEqual(f.of("⚠️ **這一輪中斷了：API 錯誤**\n-# 按「再試一次」會請 Claude 從中斷的地方接著做", by_bot=True)["kind"], f.BOT)

    def test_the_words_bind_no_agent_by_name(self):
        f = frames
        self.assertEqual(f.worded({"kind": f.REPLY}, "#a"), "#a 的 agent 的回覆")
        self.assertEqual(f.worded({"kind": f.REPLY}, "#a", connector="claude_code"), "#a 的 agent（claude_code） 的回覆")
        self.assertEqual(f.worded({"kind": f.UNKNOWN, "why": "w"}, None), "別的頻道 的訊息（查不到是誰說的：w）")
        for kind in (f.REPLY, f.INTERIM, f.RELAYED, f.NOTICE, f.BOARD, f.TERMINAL, f.PROCESS, f.BOT, f.BOT_UNKNOWN, f.OWNER, f.OTHER, f.UNKNOWN):
            self.assertNotIn("Claude", f.worded({"kind": kind}, "#a"), kind)


if __name__ == "__main__":
    unittest.main()
