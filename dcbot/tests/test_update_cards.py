"""C-57 D2: #update's cards. The writer (altention/dc/card.py) and the reader (bot/cards.py) agree on one format; a
card is one message edited in place; its buttons do only what the bot knows (a fixed set of actions); a press works
after a restart (state/update-cards/); "update" takes the buttons away first and starts `altention update go` in a
session of its own."""
import asyncio
import json
import os
import tempfile
import unittest
from unittest import mock

import discord

from bot import config, update_cards as cards
from tests import tmuxguard  # noqa: F401,E402  (no test reaches a real tmux server)

ENGINE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def card_mod():
    import importlib.util
    import sys
    sys.path.insert(0, ENGINE)
    from altention.dc import card
    return card


GO = {"id": "go", "label": "更新", "style": "primary", "action": "update_go", "arg": "v0.3.0"}
LATER = {"id": "later", "label": "之後再說", "action": "later"}
MORE = {"id": "more", "label": "看要做什麼", "action": "details"}
ASK = {"id": "ask", "label": "請 Claude 幫忙", "style": "danger", "action": "ask_claude", "arg": "請讀 /x/y.md，照 update skill 處理"}


class Writer(unittest.TestCase):
    def test_a_card_post_and_what_is_refused(self):
        card = card_mod()
        post = card.build("update-v0.3.0", "🆕 有新版 v0.3.0", [GO, LATER, MORE], details="細節", channel=None, host="lap")
        self.assertEqual(post["card"]["id"], "update-v0.3.0")
        self.assertEqual([b["action"] for b in post["card"]["buttons"]], ["update_go", "later", "details"])
        self.assertEqual(post["card"]["buttons"][1]["style"], "secondary", "the default style")
        self.assertIsNone(post["card"]["buttons"][1]["arg"])
        self.assertEqual((post["card"]["details"], post["card"]["close"]), ("細節", False))
        with mock.patch("altention.config.setting", lambda p, d=None: "news" if p == "core.update_channel" else None):
            self.assertEqual(card.build("x", "t")["channel"], "news")
        for bad in (dict(card_id="a b"), dict(card_id="x" * 41), dict(text=""), dict(buttons=[GO] * 6),
                    dict(buttons=[dict(GO, action="run_shell")]), dict(buttons=[dict(GO, arg="main")]),
                    dict(buttons=[dict(GO, arg="v0.3.0; rm -rf ~")]), dict(buttons=[GO, dict(LATER, id="go")]),
                    dict(buttons=[dict(GO, id="x" * 21)]), dict(buttons=[dict(GO, label="")]), dict(buttons=[dict(GO, style="red")]),
                    dict(buttons=[dict(ASK, arg=" ")]), dict(details="d" * 8001)):
            args = dict(card_id="update-v0.3.0", text="t", buttons=[])
            args.update(bad)
            with self.assertRaises(card.CardError, msg=bad):
                card.build(args["card_id"], args["text"], args["buttons"], details=args.get("details"))
        long = card.build("x", "字" * 3000)["card"]["text"]
        self.assertLessEqual(len(long), card.TEXT_MAX)
        self.assertIn("截掉", long)
        closed = card.build("x", None, [GO], close=True)["card"]
        self.assertEqual((closed["buttons"], closed["close"], closed["text"]), ([], True, None))

    def test_a_refused_delivery_raises_with_why(self):
        card = card_mod()
        with mock.patch.object(card.dcpost, "deliver", return_value=(1, "altention dc post：連不上 bot\n")):
            with self.assertRaises(card.CardError) as e:
                card.show("x", "t")
        self.assertIn("連不上", str(e.exception))
        with mock.patch.object(card.dcpost, "deliver", return_value=(0, "已送出到 #update")):
            self.assertTrue(card.show("x", "t"))

        def no_bot(post, capture=False):
            raise SystemExit("altention dc post：找不到負責 Discord 機器人的機器")
        with mock.patch.object(card.dcpost, "deliver", no_bot):
            with self.assertRaises(card.CardError, msg="#code-review-2 of 12a1556: not a SystemExit past the caller") as e:
                card.show("x", "t")
        self.assertIn("找不到負責 Discord 機器人的機器", str(e.exception))

        def gone():  # the machine list names no bot machine: what bot_machine() does then
            raise SystemExit("altention dc post：找不到負責 Discord 機器人的機器（機器清單沒有 dcbot 角色）")
        with mock.patch.object(card.dcpost, "bot_machine", gone):
            self.assertEqual(card.dcpost.deliver({"channel": "x", "text": "t"}, capture=True)[0], 1, "deliver itself never exits")
            with self.assertRaises(card.CardError) as e:
                card.show("x", "t")
        self.assertIn("機器清單沒有 dcbot 角色", str(e.exception))

    def test_dcctl_queues_a_card_for_a_channel_not_there_yet(self):
        import importlib.machinery
        import importlib.util
        loader = importlib.machinery.SourceFileLoader("dcctl_t", os.path.join(config.ROOT, "tools", "dcctl"))
        spec = importlib.util.spec_from_loader("dcctl_t", loader)
        dcctl = importlib.util.module_from_spec(spec)
        loader.exec_module(dcctl)
        self.assertIsNone(dcctl.post_target_error({"channel": "update", "card": {"id": "x"}}, None, []))
        self.assertIsNotNone(dcctl.post_target_error({"channel": "update", "text": "x"}, None, []))


class Msg(object):
    def __init__(self, ch, mid, content, view):
        self.ch, self.id, self.content, self.view = ch, mid, content, view


class Chan(object):
    def __init__(self, cid, name):
        self.id, self.name, self.msgs, self.extra, self.gone = cid, name, {}, [], set()

    async def send(self, content=None, view=None, file=None, reference=None, **kw):
        if view is not None or file is None and reference is None and content and not content.startswith("要做"):
            m = Msg(self, 100 + len(self.msgs) + len(self.extra), content, view)
            self.msgs[m.id] = m
            return m
        self.extra.append((content, getattr(file, "filename", None), reference))
        return Msg(self, 0, content, None)

    def get_partial_message(self, mid):
        ch = self

        class Partial(object):
            async def edit(self, content=None, view=None):
                if getattr(ch, "fail_next", 0):
                    ch.fail_next -= 1
                    raise discord.HTTPException(mock.Mock(status=503, reason="Service Unavailable"), "upstream connect error")
                if mid in ch.gone or mid not in ch.msgs:
                    raise discord.NotFound(mock.Mock(status=404, reason="Not Found"), "Unknown Message")
                ch.msgs[mid].content, ch.msgs[mid].view = content, view
                return ch.msgs[mid]
        return Partial()


class FakePosts(object):
    def __init__(self, bot):
        self.bot = bot

    async def channel(self, ref, sender_ch=None, create=True):
        return next((c for c in self.bot.chans.values() if c.name == str(ref).lstrip("#")), None)

    async def notify_channel(self, post):
        return self.bot.chans[3], "（原本要送到 #%s，找不到這個頻道，改送到這裡）\n" % post["channel"]


class Bot(object):
    def __init__(self, with_update=True):
        self.chans = {3: Chan(3, "notifications")}
        if with_update:
            self.chans[1] = Chan(1, config.UPDATE_CHANNEL)
        self.audits, self.submitted, self.channels_state = [], [], {}
        self.posts = FakePosts(self)
        bot = self

        class Tmux(object):
            async def submit(self, message, text):
                bot.submitted.append((message.channel.id, text))
        self.tmux = Tmux()

    def get_channel(self, cid):
        return self.chans.get(cid)

    def get_guild(self, gid):
        return type("G", (), {"text_channels": list(self.chans.values())})()

    async def audit(self, text):
        self.audits.append(text)


class Reader(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.patch = mock.patch.object(cards, "FOLDER", os.path.join(tmp.name, "update-cards"))
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.bot = Bot()
        self.cards = cards.Cards(self.bot)

    def show(self, *a, **kw):
        post = card_mod().build(*a, host="lap", **kw)
        json.loads(json.dumps(post))  # what the spool holds
        asyncio.run(self.cards.show(post))

    def only(self, ch):
        (m,), = [list(ch.msgs.values())]
        return m

    def test_one_card_one_message_edited_in_place_and_posted_again_if_deleted(self):
        upd = self.bot.chans[1]
        self.show("update-v0.3.0", "🆕 有新版", [GO, LATER])
        m = self.only(upd)
        self.assertEqual([b.custom_id for b in m.view.children], ["upd:update-v0.3.0:go", "upd:update-v0.3.0:later"])
        self.assertEqual(m.view.children[0].style, discord.ButtonStyle.primary)
        self.show("update-v0.3.0", "⏳ 先試一次…", [])
        self.assertEqual(self.only(upd).content, "⏳ 先試一次…")
        self.assertIsNone(self.only(upd).view, "no buttons: none shown")
        upd.gone.add(m.id)
        self.show("update-v0.3.0", "✅ 更新好了", [])
        self.assertEqual(len(upd.msgs), 2, "deleted by hand: posted again")
        self.assertEqual(cards.load("update-v0.3.0")["message"], max(upd.msgs))
        card_mod()
        asyncio.run(self.cards.show(card_mod().build("update-v0.3.0", None, close=True, host="lap")))
        self.assertEqual(upd.msgs[max(upd.msgs)].content, "✅ 更新好了", "close without text keeps the text")

    def test_no_update_channel_the_notification_channel_said_so(self):
        self.bot = Bot(with_update=False)
        self.cards = cards.Cards(self.bot)
        self.show("update-v0.3.0", "🆕 有新版", [GO])
        m = self.only(self.bot.chans[3])
        self.assertIn("原本要送到 #%s" % config.UPDATE_CHANNEL, m.content)
        self.assertIn("🆕 有新版", m.content)
        self.show("update-v0.3.0", "⏳", [])
        self.assertEqual(len(self.bot.chans[3].msgs), 1, "stays where it is")

    def test_a_button_of_an_action_the_bot_does_not_know_is_not_shown(self):
        post = card_mod().build("c", "t", [GO], host="lap")
        post["card"]["buttons"].append({"id": "sh", "label": "run", "action": "run_shell", "arg": "rm -rf ~"})
        post["card"]["buttons"].append(dict(GO, id="go2", arg="v1; touch /tmp/x"))
        asyncio.run(self.cards.show(post))
        self.assertEqual([b.custom_id for b in self.only(self.bot.chans[1]).view.children], ["upd:c:go"])
        self.assertIn("2 顆按鈕機器人不認得", self.bot.audits[-1])
        asyncio.run(self.cards.show({"card": {"id": "../x", "text": "t"}, "channel": "update"}))
        self.assertIn("id 不能用", self.bot.audits[-1])


class Retry(Reader):
    """#code-review of 12a1556 (manager 2026-10-19): a card post Discord fails (a 503, not NotFound) is kept and shown
    on the next round, never dropped: the last word of an update is not lost; a post that comes late never puts back
    older words; still one message."""

    def test_a_503_on_the_last_show_is_shown_on_the_next_round(self):
        import time
        from bot import posts, postbox
        with tempfile.TemporaryDirectory() as d:
            spool, failed = os.path.join(d, "posts"), os.path.join(d, "posts-failed")
            self.bot.cards = self.cards
            real = posts.Posts(self.bot)
            upd = self.bot.chans[1]

            def through_spool(post):
                postbox.write(post, spool)
                (path, got), = postbox.waiting(spool)
                with mock.patch.object(postbox, "FAILED", failed):
                    asyncio.run(real.safely(got, path))
            card = card_mod()
            through_spool(card.build("update-v0.3.0", "⏳ 更新中…", [], host="lap"))
            older = card.build("update-v0.3.0", "⏳ 還在裝…", [], host="lap")
            time.sleep(0.01)
            upd.fail_next = 1
            through_spool(card.build("update-v0.3.0", "✅ 更新好了：v0.3.0", [], host="lap"))
            self.assertEqual(self.only(upd).content, "⏳ 更新中…", "Discord failed: not shown yet")
            self.assertEqual(len(os.listdir(failed)), 1, "kept, not dropped")
            self.assertEqual(os.listdir(spool), [])
            with mock.patch.object(postbox, "FAILED", failed):
                asyncio.run(real.retry())
            self.assertEqual(self.only(upd).content, "✅ 更新好了：v0.3.0", "the next round shows it")
            self.assertEqual([n for n in os.listdir(failed) if not n.startswith(".")], [])
            self.assertEqual(len(upd.msgs), 1, "the same message, no second one")
            through_spool(older)  # written before the ✅, delivered after it (a retry come late)
            self.assertEqual(self.only(upd).content, "✅ 更新好了：v0.3.0", "older words never come back")
            upd.gone.add(next(iter(upd.msgs)))
            through_spool(card.build("update-v0.3.0", "✅ 更新好了（再說一次）", [], host="lap"))
            self.assertEqual(len(upd.msgs), 2, "NotFound (deleted by hand): posted again at once, as before")
            self.assertEqual([n for n in os.listdir(failed) if not n.startswith(".")], [])


class Press(Reader):
    def press(self, card_id, bid, who=None, cards_obj=None):
        test, got = self, []

        class Response(object):
            async def defer(self):
                got.append("defer")

            async def edit_message(self, view=None):
                got.append(("edit", view))

            async def send_message(self, text, ephemeral=False):
                got.append(("say", text))

        clicker = type("U", (), {"id": config.OWNER_ID if who is None else who})()

        class Click(object):
            type = discord.InteractionType.component
            data = {"custom_id": "upd:%s:%s" % (card_id, bid)}
            user = clicker
            channel = test.bot.chans[1]
            response = Response()
        handled = asyncio.run((cards_obj or self.cards).on_interaction(Click()))
        return handled, got

    def test_update_takes_the_buttons_away_first_then_starts_and_works_after_a_restart(self):
        self.show("update-v0.3.0", "🆕 有新版", [GO, LATER])
        seen = []

        def start(version, card_id):
            seen.append((version, card_id, cards.load(card_id)["buttons"], self.only(self.bot.chans[1]).view))
            return 4242
        restarted = cards.Cards(self.bot)  # a new bot process: only the files
        with mock.patch.object(cards, "start_update", start):
            handled, got = self.press("update-v0.3.0", "go", cards_obj=restarted)
        self.assertTrue(handled)
        self.assertEqual(seen, [("v0.3.0", "update-v0.3.0", [], None)], "buttons gone before it starts")
        self.assertIn("⏳ 開始更新", self.only(self.bot.chans[1]).content)
        with mock.patch.object(cards, "start_update", start):
            handled, got = self.press("update-v0.3.0", "go")
        self.assertEqual(len(seen), 1, "a second press of an old row does nothing")
        self.assertIn(("say", "這顆按鈕已經換掉了，看卡片現在的樣子。"), got)

    def test_only_the_owner_and_other_prefixes_are_not_ours(self):
        self.show("c", "t", [LATER])
        handled, got = self.press("c", "later", who=config.OWNER_ID + 1)
        self.assertEqual(got, [("say", "只有擁有者能用。")])
        self.assertIsNotNone(self.only(self.bot.chans[1]).view, "nothing done")

        class Other(object):
            type = discord.InteractionType.component
            data = {"custom_id": "ask:1:2"}
        self.assertFalse(asyncio.run(self.cards.on_interaction(Other())))

    def test_a_start_that_fails_is_said_on_the_card(self):
        self.show("c", "t", [GO])

        def start(version, card_id):
            raise OSError("No such file or directory: bin/altention")
        with mock.patch.object(cards, "start_update", start):
            self.press("c", "go")
        self.assertIn("❌ 更新沒有開始", self.only(self.bot.chans[1]).content)

    def test_later_and_details(self):
        self.show("c", "🆕", [LATER, MORE], details="短的細節")
        self.press("c", "more")
        self.assertEqual(self.bot.chans[1].extra[-1][0], "短的細節")
        self.assertIsNotNone(self.only(self.bot.chans[1]).view, "details leaves the card as it is")
        self.show("c", "🆕", [LATER, MORE], details="長" * 3000)
        self.press("c", "more")
        self.assertEqual(self.bot.chans[1].extra[-1][1], "details.txt")
        self.press("c", "later")
        m = self.only(self.bot.chans[1])
        self.assertIsNone(m.view)
        self.assertIn("之後再說", m.content)
        self.assertNotIn("commit", m.content)

    def test_ask_claude_to_the_setup_channel_else_a_file_and_never_too_long(self):
        self.show("c", "❌ 沒有更新", [ASK])
        self.press("c", "ask")
        self.assertEqual(self.bot.submitted, [])
        self.assertEqual(self.bot.chans[1].extra[-1][1], "for-claude.txt")
        self.assertIn("還沒有 #%s" % config.SETUP_CHANNEL, self.only(self.bot.chans[1]).content)
        self.bot.chans[5] = Chan(5, config.SETUP_CHANNEL)  # there, its conversation not set up yet
        self.show("c", "❌ 沒有更新", [ASK])
        self.press("c", "ask")
        self.assertEqual(self.bot.submitted, [])
        self.assertIn("#%s 頻道還沒有可以交給的 Claude" % config.SETUP_CHANNEL, self.only(self.bot.chans[1]).content)
        self.assertNotIn("還沒有 #%s 頻道" % config.SETUP_CHANNEL, self.only(self.bot.chans[1]).content)
        self.bot.channels_state["5"] = {"configured": True}
        self.show("c", "❌ 沒有更新", [ASK])
        self.press("c", "ask")
        self.assertEqual(self.bot.submitted, [(5, ASK["arg"])])
        self.assertIn("<#5>", self.only(self.bot.chans[1]).content)
        self.assertIsNone(self.only(self.bot.chans[1]).view)
        self.show("c", "❌", [dict(ASK, arg="字" * 6001)])
        self.press("c", "ask")
        self.assertEqual(len(self.bot.submitted), 1)
        self.assertIn("太長（6001 字），沒有送出", self.only(self.bot.chans[1]).content)


class Start(unittest.TestCase):
    def test_the_update_runs_in_a_session_of_its_own_and_writes_the_log(self):
        """What lets it outlive the bot it restarts: setsid (a new session), no terminal, output to the log."""
        import time
        with tempfile.TemporaryDirectory() as d:
            engine, home = os.path.join(d, "engine"), os.path.join(d, "home")
            os.makedirs(os.path.join(engine, "bin"))
            out = os.path.join(d, "ran.json")
            script = os.path.join(engine, "bin", "altention")
            with open(script, "w") as f:
                f.write("#!/usr/bin/env python3\nimport json, os, sys\njson.dump({'argv': sys.argv[1:], 'sid': os.getsid(0), "
                        "'pid': os.getpid(), 'cwd': os.getcwd()}, open(%r, 'w'))\nprint('update go started')\n" % out)
            os.chmod(script, 0o755)
            with mock.patch.object(cards, "ENGINE", engine), mock.patch.dict(os.environ, {"HOME": home}):
                pid = cards.start_update("v0.3.0", "update-v0.3.0")
                for _ in range(100):
                    if os.path.exists(out):
                        break
                    time.sleep(0.05)
                os.waitpid(pid, 0)
                ran = json.load(open(out))
                self.assertEqual(ran["argv"], ["update", "go", "v0.3.0", "--card", "update-v0.3.0"])
                self.assertEqual(ran["sid"], ran["pid"], "a session of its own")
                self.assertNotEqual(ran["sid"], os.getsid(0))
                self.assertEqual(os.path.realpath(ran["cwd"]), os.path.realpath(engine))
                log = open(cards.log_path()).read()
                self.assertIn("按下「更新」：v0.3.0", log)
                self.assertIn("update go started", log)
                self.assertTrue(cards.log_path().startswith(home))
