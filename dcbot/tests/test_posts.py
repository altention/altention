"""Posting to channels: records, handing posts to a channel's Claude, the loop guard, and dcpost's command line."""
import asyncio
import contextlib
import importlib.machinery
import importlib.util
import io
import os
import re
import tempfile
import time
import unittest
from unittest import mock

from bot import posts
from tests import tmuxguard  # noqa: F401,E402  (no test reaches a real tmux server)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LINE = re.compile(r"line \d{3} x+")  # the rows of a long notification, wherever they were sent


class Chan(object):
    def __init__(self, cid, name):
        self.id, self.name, self.sent = cid, name, []
        self.fails = None  # an exception each send raises; or a number: that many sends go, then the rest fail

    async def send(self, text=None, view=None, file=None, embed=None, silent=False, files=None, allowed_mentions=None):
        if isinstance(self.fails, int) and not isinstance(self.fails, bool):
            if len(self.sent) >= self.fails:
                raise OSError("503 Service Unavailable")
        elif self.fails is not None:
            raise self.fails
        self.sent.append(text if embed is None else "%s\n%s" % (embed.author.name, embed.description))
        self.how = getattr(self, "how", []) + [{"files": [f.filename for f in files or ([file] if file else [])],
                                                "mentions": allowed_mentions, "embed": embed is not None}]
        return type("Sent", (), {"id": len(self.sent)})()


class Guild(object):
    def __init__(self, bot):
        self.bot, self.text_channels, self.categories = bot, [], []

    async def create_text_channel(self, name, topic=None, category=None):
        ch = Chan(900 + len(self.bot.chans), name)
        ch.category = category
        self.bot.chans[ch.id] = ch
        self.text_channels.append(ch)
        return ch

    async def create_category(self, name):
        cat = type("Cat", (), {"name": name})()
        self.categories.append(cat)
        return cat


class Tmux(object):
    def __init__(self):
        self.given = []

    async def submit(self, message, text):
        self.given.append((message.channel.id, text))


class Bot(object):
    def __init__(self):
        self.chans, self.channels_state, self.tmux, self.audits = {}, {}, Tmux(), []
        self.guild = Guild(self)
        self.audit_ch = Chan(99, "audit")

    def get_channel(self, cid):
        return self.chans.get(cid)

    def get_guild(self, gid):
        return self.guild

    def chan(self, ch):
        return self.channels_state.setdefault(str(ch.id), {})

    def save(self):
        pass

    async def audit(self, text):
        self.audits.append(text)


class Posts(unittest.TestCase):
    def setUp(self):
        self.bot = Bot()
        self.a, self.b, self.notes = Chan(1, "st-1"), Chan(2, "st-0"), Chan(3, "notes")
        for c in (self.a, self.b, self.notes):
            self.bot.chans[c.id] = c
        self.bot.guild.text_channels = [self.a, self.b, self.notes]
        self.bot.channels_state = {"1": {"pane": {"id": "%1"}}, "2": {"tmux": "dc-x"}, "3": {"plain": True}}
        self.p = posts.Posts(self.bot)

    def post(self, **kw):
        asyncio.run(self.p.handle(kw))

    def test_claude_to_claude_is_shown_and_handed_over(self):
        self.post(channel="st-0", text="我看完了", from_channel="1")
        self.assertEqual(self.b.sent, ["📨 來自 #st-1\n我看完了"])
        (cid, text), = self.bot.tmux.given
        self.assertEqual(cid, 2)
        self.assertIn("這是 #st-1 傳到這個頻道的訊息", text)
        self.assertIn("altention dc post 1 ", text, "the reply goes by ID: still right after a rename")
        self.assertTrue(text.endswith("我看完了"))

    def test_records_are_only_kept(self):
        self.post(channel="#notes", text="report", from_name="HostB 上的 Claude（x）")
        self.post(channel="no-such", text="lost")
        self.assertEqual(len(self.bot.guild.text_channels), 3, "an unknown name is not made without --create")
        self.post(channel="brand-new", text="first", create=True)
        self.assertEqual(self.notes.sent, ["📨 來自 HostB 上的 Claude（x）\nreport"])
        new = self.bot.guild.text_channels[-1]
        self.assertEqual((new.name, new.sent), ("brand-new", ["📨 來自 別的地方\nfirst"]))
        self.assertTrue(self.bot.channels_state[str(new.id)]["plain"], "a channel made by dcpost is for records")
        self.assertEqual(self.bot.tmux.given, [])

    def test_a_named_category_gets_the_new_channel(self):
        class Cat(object):
            name = "日常"
        self.bot.guild.categories = [Cat()]
        self.post(channel="日常/用量", text="x", create=True)
        new = self.bot.guild.text_channels[-1]
        self.assertEqual((new.name, new.category.name), ("用量", "日常"))
        before = len(self.bot.guild.text_channels)
        self.post(channel="沒有這個分類/x", text="y")
        self.assertEqual(len(self.bot.guild.text_channels), before)
        self.assertIn("找不到頻道", self.bot.audits[-1])

    def test_notifications_are_only_shown(self):
        self.post(channel="st-0", text="訓練跑完了", from_name="HostB", notify=True)
        self.assertEqual(self.b.sent, ["🔔 **[HostB]** 訓練跑完了"])
        self.assertEqual(self.bot.tmux.given, [], "a notification is not handed to the channel's Claude")

    def notify(self, default, **kw):
        with mock.patch.object(posts, "notify_default", return_value=default):
            self.post(notify=True, from_name="HostC", **kw)

    def test_a_notification_for_a_channel_that_is_not_there_goes_to_the_default_saying_so(self):
        """#issues 7 (2026-10-11): it was dropped with one line in #audit, and dcnotify said it went."""
        before = len(self.bot.guild.text_channels)
        self.notify("notes", channel="exp-alerts", text="loss nan")
        self.assertEqual(self.notes.sent, ["🔔 **[HostC]** （原本要送到 #exp-alerts，找不到這個頻道，改送到這裡）\nloss nan"])
        self.assertEqual(len(self.bot.guild.text_channels), before, "the channel it names is not made")
        self.assertIn("找不到頻道 #exp-alerts，通知改送到 #notes", self.bot.audits[-1])
        self.assertEqual(self.bot.tmux.given, [])
        self.notify("notes", channel="#exp-alerts", text="again")  # as -c "#name"
        self.notify("notes", channel="777", text="by an id that is gone")
        self.notify("notes", channel="沒有這個分類/x", text="a category that is not there")
        self.assertIn("原本要送到 #exp-alerts", self.notes.sent[1])
        self.assertIn("原本要送到 777，", self.notes.sent[2])
        self.assertIn("原本要送到 #沒有這個分類/x", self.notes.sent[3])
        self.assertEqual(len(self.bot.guild.text_channels), before)

    def test_the_default_in_a_category(self):
        class Cat(object):
            name = "系統"
        self.notes.category = Cat()
        self.bot.guild.categories = [self.notes.category]
        self.notify("系統/notes", channel="gone", text="x")
        self.assertEqual(len(self.notes.sent), 1)
        self.bot.guild.categories, self.notes.category = [], None  # the default's category was removed: by its name
        self.notify("系統/notes", channel="gone", text="y")
        self.assertEqual(len(self.notes.sent), 2)

    def test_only_a_default_that_is_not_there_is_made(self):
        before = len(self.bot.guild.text_channels)
        self.notify("alerts", channel="exp-alerts", text="loss nan")
        new = self.bot.guild.text_channels[-1]
        self.assertEqual((len(self.bot.guild.text_channels), new.name), (before + 1, "alerts"))
        self.assertTrue(self.bot.channels_state[str(new.id)]["plain"])
        self.assertEqual(new.sent, ["🔔 **[HostC]** （原本要送到 #exp-alerts，找不到這個頻道，改送到這裡）\nloss nan"])
        self.assertTrue(any("預設的通知頻道 alerts 不存在，已建立 #alerts" in a for a in self.bot.audits))
        self.notify("alerts", channel="alerts", text="to the default itself")  # there now: nothing made, no note
        self.assertEqual((len(self.bot.guild.text_channels), new.sent[-1]), (before + 1, "🔔 **[HostC]** to the default itself"))

    def test_the_default_itself_named_and_not_there_is_made_without_a_note(self):
        for default, named in (("系統/alerts", "系統/alerts"), ("系統/alerts", "alerts"), ("alerts", "")):
            self.setUp()
            self.notify(default, channel=named, text="x")
            new = self.bot.guild.text_channels[-1]
            self.assertEqual((new.name, new.sent), ("alerts", ["🔔 **[HostC]** x"]), (default, named))

    def test_a_notification_is_never_dropped_even_when_no_channel_can_be_had(self):
        async def refuses(*a, **k):
            raise RuntimeError("Missing Permissions")
        self.bot.guild.create_text_channel = refuses
        with contextlib.redirect_stderr(io.StringIO()):
            self.notify("alerts", channel="exp-alerts", text="disk full on HostC")
        said = self.bot.audit_ch.sent[-1]
        self.assertIn("送不出去", said)
        self.assertIn("disk full on HostC", said, "the words themselves are kept where the owner can read them")
        self.assertIn("Missing Permissions", said)

    def quietly(self, default, **kw):
        with contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
            with mock.patch.object(posts, "notify_default", return_value=default):
                try:
                    asyncio.run(self.p.handle(dict(kw, notify=True, from_name="HostC")))
                except posts.Undelivered as e:
                    return e.post
        return None

    def test_a_channel_that_does_not_take_it_sends_it_to_the_default(self):
        """Manager 2026-10-11: with the channel there and its send failing (a 5xx, no permission to speak there)
        the notification was gone without a line anywhere."""
        for what in (OSError("503 Service Unavailable"), RuntimeError("403 Forbidden (error code: 50013): Missing Permissions")):
            self.setUp()
            self.b.fails = what
            self.assertIsNone(self.quietly("notes", channel="st-0", text="訓練跑完了"))
            self.assertEqual(self.b.sent, [])
            self.assertEqual(self.notes.sent, ["🔔 **[HostC]** （原本要送到 #st-0，送不出去，改送到這裡）\n訓練跑完了"])
            self.assertIn("#st-0 送不出去", self.bot.audits[-1])
            self.assertIn(str(what)[:20], self.bot.audits[-1])
            self.assertIn("通知改送到 #notes", self.bot.audits[-1])

    def test_an_id_that_is_a_categorys(self):
        self.bot.chans[50] = type("Category", (), {"id": 50, "name": "專案"})()  # get_channel gives it: it has no send
        self.assertIsNone(self.quietly("notes", channel="50", text="x"))
        self.assertEqual(self.notes.sent, ["🔔 **[HostC]** （原本要送到 50，送不出去，改送到這裡）\nx"])

    LONG = "\n".join("line %03d %s" % (i, "x" * 60) for i in range(60))  # three messages

    def test_stopping_halfway_the_rest_goes_on_marked_as_a_continuation(self):
        self.b.fails = 1  # the first message goes, then the channel fails
        self.assertIsNone(self.quietly("notes", channel="st-0", text=self.LONG))
        self.assertEqual(len(self.b.sent), 1)
        self.assertTrue(self.notes.sent[0].startswith("🔔 **[HostC]** （原本要送到 #st-0，送不出去，改送到這裡）\n（接續：前面的部分已送到 #st-0）\n"))
        lines = LINE.findall("\n".join(self.b.sent + self.notes.sent))
        self.assertEqual("\n".join(lines), self.LONG, "every line once, in order: none lost, none twice")

    def test_the_default_failing_too_puts_the_words_in_audit(self):
        self.b.fails = self.notes.fails = OSError("503")
        self.assertIsNone(self.quietly("notes", channel="st-0", text="disk full"))
        said = "\n".join(self.bot.audit_ch.sent)
        self.assertIn("⚠️ altention dc notify 的通知送不出去", said)
        self.assertIn("#st-0 送不出去", said)
        self.assertIn("預設的通知頻道 #notes 送不出去", said)
        self.assertIn("來自 HostC", said)
        self.assertIn("disk full", said)

    def test_the_channel_being_the_default_goes_straight_to_audit(self):
        self.notes.fails = OSError("503")
        self.assertIsNone(self.quietly("notes", channel="notes", text="disk full"))
        self.assertIn("disk full", "\n".join(self.bot.audit_ch.sent))
        self.assertNotIn("預設的通知頻道 #notes", "\n".join(self.bot.audit_ch.sent), "not tried twice")

    def test_nothing_reachable_is_not_done(self):
        self.b.fails = self.notes.fails = self.bot.audit_ch.fails = OSError("Cannot connect to host discord.com")
        left = self.quietly("notes", channel="st-0", text="disk full")
        self.assertEqual((left["text"], left["channel"], left["continued"]), ("disk full", "st-0", False))
        self.b.fails, self.notes.fails = 1, 1  # each takes one message, then Discord goes away
        left = self.quietly("notes", channel="st-0", text=self.LONG)
        self.assertTrue(left["continued"])
        sent = LINE.findall("\n".join(self.b.sent + self.notes.sent))
        self.assertEqual("\n".join(sent + [left["text"]]), self.LONG, "what went and what is kept make the whole, nothing twice")
        self.bot.audit_ch = None  # the bot before it found #audit
        self.assertEqual(self.quietly("notes", channel="st-0", text="again")["text"], "again")

    def test_the_default_in_a_category_that_is_there_is_made_in_it(self):
        """Manager 2026-10-11: not put into another project's channel of the same name."""
        class Cat(object):
            name = "系統"
        self.bot.guild.categories = [Cat()]
        self.notes.name, self.notes.category = "notifications", type("Cat", (), {"name": "project-a"})()
        self.assertIsNone(self.quietly("系統/notifications", channel="gone", text="x"))
        new = self.bot.guild.text_channels[-1]
        self.assertEqual((new.name, new.category.name, self.notes.sent), ("notifications", "系統", []))
        self.assertEqual(new.sent, ["🔔 **[HostC]** （原本要送到 #gone，找不到這個頻道，改送到這裡）\nx"])

    def test_a_post_that_is_not_a_notification_is_still_refused(self):
        before = len(self.bot.guild.text_channels)
        with mock.patch.object(posts, "notify_default", return_value="notes"):
            self.post(channel="exp-alerts", text="x")
            self.post(channel="exp-alerts", text="x", notify=True, board="b")
        self.assertEqual((self.notes.sent, len(self.bot.guild.text_channels)), ([], before))
        self.assertIn("altention dc post：找不到頻道", self.bot.audits[-1])

    def test_the_default_is_read_from_the_settings(self):
        fake = type("Alt", (), {"setting": staticmethod(lambda key: {"core.notify_channel": "系統/notifications"}.get(key))})
        with mock.patch.object(posts, "remote", create=True), mock.patch("bot.remote.alt", return_value=fake):
            self.assertEqual(posts.notify_default(), "系統/notifications")
        with mock.patch("bot.remote.alt", side_effect=RuntimeError("settings broken")):
            self.assertEqual(posts.notify_default(), "notifications")

    def test_same_named_channels_prefer_the_senders_category(self):
        class Cat(object):
            def __init__(self, name):
                self.name = name
        daily, project_b = Chan(10, "notifications"), Chan(11, "notifications")
        daily.category, project_b.category = Cat("日常"), Cat("project-b")
        sender = Chan(12, "real-stochastic-0")
        sender.category = Cat("project-b")
        chans = [daily, project_b, sender]
        self.assertIs(posts.pick_channel(chans, "notifications", sender_category="project-b"), project_b)
        self.assertIsNone(posts.pick_channel(chans, "notifications", sender_category="project-a"), "two and nothing to choose: none")
        self.assertIsNone(posts.pick_channel(chans, "notifications"))
        self.assertIs(posts.pick_channel(chans, "notifications", category="project-b"), project_b)
        self.assertIsNone(posts.pick_channel(chans, "notifications", category="nowhere"))
        self.bot.guild.text_channels = chans
        for c in chans:
            self.bot.chans[c.id] = c
        self.post(channel="notifications", text="from project-b", from_channel="12", notify=True, from_name="HostC")
        self.assertEqual(project_b.sent, ["🔔 **[HostC]** from project-b"])
        self.post(channel="project-b/notifications", text="explicit", notify=True, from_name="x")
        self.assertEqual(len(project_b.sent), 2)

    def test_attach_request_attaches_the_pane_and_tells_the_sender(self):
        from bot import remote, tmuxio
        asked = []

        async def panes(everywhere=False):
            return [{"host": "mac", "socket": "/tmp/tmux-501/default", "id": "%4", "session": "cv", "window": "0"}]

        async def attach(ch, ref, project=""):
            asked.append((ch, ref, project))
            return "已接上 `mac · cv:0`：<#77>"
        old = tmuxio.claude_panes, remote.machine_of
        tmuxio.claude_panes, remote.machine_of = panes, lambda h: "mac" if h == "alices-MacBook-Pro" else None
        self.bot.attach = attach
        try:
            self.post(channel="cv", attach={"socket": "/private/tmp/tmux-501/default", "pane": "%4"},
                      from_host="alices-MacBook-Pro", from_channel="2", text="x")
        finally:
            tmuxio.claude_panes, remote.machine_of = old
        self.assertEqual(asked, [(None, "mac|/tmp/tmux-501/default|%4", "cv")])
        self.assertIn("已接上", self.bot.tmux.given[-1][1])
        self.assertEqual(self.bot.guild.text_channels, [self.a, self.b, self.notes], "no channel called cv was made")

    def test_new_claude_makes_a_channel_with_its_own_claude(self):
        started = []

        class Cat(object):
            name = "日常"

        async def start_now(ch):
            started.append(ch.id)
        self.bot.tmux.start_now = start_now
        self.bot.guild.categories = [Cat()]
        with tempfile.TemporaryDirectory() as folder:
            self.post(channel="日常/英文", new_claude=folder, text="英文家教", from_channel="2")
            new = self.bot.guild.text_channels[-1]
            st = self.bot.channels_state[str(new.id)]
            self.assertEqual((new.name, new.category.name, st["cwd"], st.get("plain")), ("英文", "日常", folder, None))
            self.assertEqual(started, [new.id])
            self.assertIn("已建好 #英文", self.bot.tmux.given[-1][1])
            self.post(channel="日常/英文", new_claude=folder, from_channel="2")
            self.assertIn("已經設定過了", self.bot.tmux.given[-1][1])
        self.post(channel="日常/x", new_claude="/no/such/folder", from_channel="2")
        self.assertIn("找不到資料夾", self.bot.tmux.given[-1][1])
        with tempfile.TemporaryDirectory() as folder:
            self.post(channel="Project6/deep", new_claude=folder, from_channel="2")
        self.assertEqual(self.bot.guild.text_channels[-1].category.name, "Project6", "a missing category is made")

    def test_resume_and_history_requests(self):
        asked = []

        async def resume(ch, st, ref):
            asked.append((ch.id, ref))
            return "已切到對話 `eb106548`"
        self.bot.resume = resume
        self.post(channel="st-0", resume="eb106548", from_channel="1")
        self.assertEqual(asked, [(2, "eb106548")])
        self.assertIn("已切到對話", self.bot.tmux.given[-1][1])
        self.post(channel="st-1", resume="eb106548", from_channel="2")
        self.assertIn("不能從這裡換對話", self.bot.tmux.given[-1][1], "an attached pane is the owner's terminal")
        self.post(channel="notes", history=True, from_channel="2")
        self.assertIn("找不到有 Claude 的頻道", self.bot.tmux.given[-1][1])

    def test_resume_and_history_for_a_window_on_another_machine(self):
        """#issues 35: such a channel was refused as "somebody else's conversation" (its pane names a machine). It is
        the bot's own window: --resume goes the way !resume goes, --history asks that machine for the file of the
        conversation the channel is on and for its history, forced."""
        from bot import history
        sid = "0f6e1c1e-1111-4222-8333-444455556666"
        self.bot.channels_state["2"] = {"cwd": "/far/proj", "session_id": sid, "home": {"host": "lap", "session": "work", "window": "w"},
                                        "pane": {"host": "lap", "socket": "/s", "id": "%1"}}
        self.bot.chan = lambda ch: self.bot.channels_state[str(ch.id)]
        asked, shown, answers = [], [], {}

        async def resume(ch, st, ref):
            asked.append((ch.id, ref))
            return "已切到對話 `eb106548`"

        async def far_sessions(host, cwd, ref=""):
            asked.append((host, cwd, ref))
            return answers.get("found", ({"id": sid, "path": "/far/x/%s.jsonl" % sid}, None))

        async def show_remote(bot, ch, st, host, session_id, path, force=False):
            shown.append((ch.id, host, session_id, path, force))
            return answers.get("done", True)
        self.bot.resume = resume
        with mock.patch.object(history, "far_sessions", far_sessions), mock.patch.object(history, "show_remote", show_remote):
            self.post(channel="st-0", resume="eb106548", from_channel="1")
            self.assertEqual(asked, [(2, "eb106548")])
            self.assertIn("已切到對話", self.bot.tmux.given[-1][1])
            self.post(channel="st-0", history=True, from_channel="1")
            self.assertEqual(asked[-1], ("lap", "/far/proj", sid))
            self.assertEqual(shown, [(2, "lap", sid, "/far/x/%s.jsonl" % sid, True)])
            self.assertIn("已在 #st-0 貼出對話 `0f6e1c1e` 的紀錄。", self.bot.tmux.given[-1][1])
            for found, words in (((None, "現在連不上"), "#st-0 的對話在 lap 上，現在連不上，所以沒有貼紀錄。"),
                                 ((None, None), "#st-0 的對話在 lap 上，那台找不到 `0f6e1c1e` 這個對話，所以沒有貼紀錄。")):
                answers["found"] = found
                self.post(channel="st-0", history=True, from_channel="1")
                self.assertIn(words, self.bot.tmux.given[-1][1])
            self.assertEqual(len(shown), 1, "nothing asked for without the file")
            answers.pop("found")
            answers["done"] = False
            self.post(channel="st-0", history=True, from_channel="1")
            self.assertIn("紀錄沒有貼成", self.bot.tmux.given[-1][1])
            self.bot.channels_state["2"] = {"cwd": "/far/proj", "pane": {"host": "lap", "socket": "/s", "id": "%1"}}
            self.post(channel="st-0", history=True, from_channel="1")
            self.assertIn("不能從這裡換對話或貼紀錄", self.bot.tmux.given[-1][1], "a pane attached on another machine is the owner's terminal still")

    def test_a_board_is_edited_in_place(self):
        class Msg(object):
            def __init__(self, ch, mid):
                self.ch, self.id = ch, mid

            async def edit(self, content=None, attachments=None):
                self.ch.boards[self.id] = content
                self.ch.files[self.id] = [a.filename for a in attachments] if attachments else self.ch.files.get(self.id)
                return self

            async def delete(self):
                del self.ch.boards[self.id]

        class BoardChan(Chan):
            def __init__(self, cid, name):
                Chan.__init__(self, cid, name)
                self.boards, self.next, self.files = {}, 100, {}

            async def send(self, text, view=None, file=None, silent=False):
                self.next += 1
                self.boards[self.next] = text
                self.files[self.next] = [file.filename] if file else None
                return Msg(self, self.next)

            def get_partial_message(self, mid):
                return Msg(self, mid)
        show = BoardChan(4, "行程")
        self.bot.chans[4] = show
        self.bot.guild.text_channels.append(show)
        self.bot.channels_state["4"] = {"plain": True}
        self.post(channel="行程", board="本週", text="- 週一 開會")
        self.post(channel="行程", board="本週", text="- 週二 交報告")
        self.assertEqual(list(show.boards), [101])
        self.assertIn("📌 **本週**", show.boards[101])
        self.assertIn("週二 交報告", show.boards[101])
        self.post(channel="行程", board="本週", text="x" * 5000)
        self.assertGreater(len(show.boards), 1)
        self.assertEqual(len(self.bot.channels_state["4"]["boards"]["本週"]), len(show.boards))
        self.post(channel="行程", board="本週", text="short")
        self.assertEqual(list(show.boards), [101])
        self.assertEqual(self.bot.channels_state["4"]["boards"], {"本週": [101]})
        import base64
        png = base64.b64encode(b"\x89PNG").decode()
        self.post(channel="行程", board="圖表", text="chart", file_b64=png, file_name="chart.png")
        self.post(channel="行程", board="圖表", text="chart 2", file_b64=png, file_name="chart.png")
        mid = self.bot.channels_state["4"]["boards"]["圖表"][0]
        self.assertEqual(show.files[mid], ["chart.png"], "the picture is replaced in place")
        self.assertEqual(self.bot.tmux.given, [])

    def test_a_board_is_posted_again_only_when_discord_says_it_is_gone(self):
        """Only NotFound posts a board message anew; a 5xx, a timeout or the network keeps its ID for next time
        (posting anew left the old copy behind, a second 圖表 board, 2026-10-17). Both are said in the log."""
        import aiohttp
        import discord
        fail = {}  # message id -> what its next edit or delete raises

        def error(kind):
            if kind == "gone":
                return discord.NotFound(type("R", (), {"status": 404, "reason": "Not Found"})(), "Unknown Message")
            if kind == "500":
                return discord.DiscordServerError(type("R", (), {"status": 500, "reason": "Server Error"})(), "boom")
            return {"timeout": asyncio.TimeoutError(), "net": aiohttp.ClientConnectionError("reset")}[kind]

        class Msg(object):
            def __init__(self, ch, mid):
                self.ch, self.id = ch, mid

            async def edit(self, content=None, attachments=None):
                if self.id in fail:
                    raise error(fail.pop(self.id))
                self.ch.boards[self.id] = content
                return self

            async def delete(self):
                if self.id in fail:
                    raise error(fail.pop(self.id))
                del self.ch.boards[self.id]

        class BoardChan(Chan):
            def __init__(self, cid, name):
                Chan.__init__(self, cid, name)
                self.boards, self.next = {}, 100

            async def send(self, text, view=None, file=None, silent=False):
                self.next += 1
                self.boards[self.next] = text
                return Msg(self, self.next)

            def get_partial_message(self, mid):
                return Msg(self, mid)
        show = BoardChan(4, "dashboard")
        self.bot.chans[4] = show
        self.bot.guild.text_channels.append(show)
        self.bot.channels_state["4"] = {"plain": True}
        ids = lambda: self.bot.channels_state["4"]["boards"]["圖表"]

        def post(text):
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.post(channel="dashboard", board="圖表", text=text)
            return out.getvalue()
        self.assertEqual(post("v1"), "")
        self.assertEqual(ids(), [101])
        for kind, name in (("500", "DiscordServerError"), ("timeout", "TimeoutError"), ("net", "ClientConnectionError")):
            fail[101] = kind
            said = post("v2 " + kind)
            self.assertEqual(ids(), [101], kind)
            self.assertEqual(list(show.boards), [101], "%s: nothing posted anew" % kind)
            self.assertIn("v1", show.boards[101], "%s: the old content stays until the next edit" % kind)
            self.assertIn("[board] #dashboard 的看板「圖表」第 1 則這次改不了（%s" % name, said)
        self.assertEqual(post("v3"), "")
        self.assertIn("v3", show.boards[101], "edited again the next time")
        fail[101] = "gone"
        del show.boards[101]
        said = post("v4")
        self.assertEqual(ids(), [102], "posted again, its new ID kept")
        self.assertIn("v4", show.boards[102])
        self.assertIn("[board] #dashboard 的看板「圖表」第 1 則不見了（", said)
        self.assertIn("Unknown Message", said)
        post("x" * 5000)
        self.assertEqual(len(ids()), len(show.boards))
        two = ids()[1]
        fail[two] = "500"
        said = post("short")
        self.assertEqual(ids(), [102, two], "a part that could not be deleted is kept, to delete it next time")
        self.assertIn("多出來的一則這次刪不掉（DiscordServerError", said)
        post("short again")
        self.assertEqual(ids(), [102])
        self.assertEqual(list(show.boards), [102])

    def test_a_board_failing_three_times_running_is_said_once(self):
        """Three times running a board does not update (not NotFound: a 403 stays a 403) is said to the owner once,
        with the board, its channel and the error; one update that works starts the count again."""
        import discord
        told, fail = [], {"on": None}

        async def notify(post, sender_ch):
            told.append(post)

        class Msg(object):
            def __init__(self, ch, mid):
                self.ch, self.id = ch, mid

            async def edit(self, content=None, attachments=None):
                if fail["on"]:
                    raise discord.Forbidden(type("R", (), {"status": 403, "reason": "Forbidden"})(),
                                            {"code": 50013, "message": "Missing Permissions"})
                return self

        class BoardChan(Chan):
            async def send(self, text, view=None, file=None, silent=False):
                return Msg(self, 101)

            def get_partial_message(self, mid):
                return Msg(self, mid)
        show = BoardChan(4, "dashboard")
        self.bot.chans[4] = show
        self.bot.guild.text_channels.append(show)
        self.bot.channels_state["4"] = {"plain": True}
        self.p.notify = notify

        def post(n=1):
            for _ in range(n):
                with contextlib.redirect_stdout(io.StringIO()):
                    self.post(channel="dashboard", board="圖表", text="x")
        post()
        fail["on"] = True
        post(2)
        self.assertEqual(told, [], "twice is not yet said")
        post()
        (said,), = [told]
        self.assertTrue(said["notify"])
        for word in ("#dashboard", "「圖表」", "連續 3 次", "HTTP 403", "50013", "Missing Permissions"):
            self.assertIn(word, said["text"])
        post(3)
        self.assertEqual(len(told), 1, "said once, not every time after")
        self.assertEqual(self.bot.channels_state["4"]["boards"]["圖表"], [101], "never posted anew")
        fail["on"] = False
        post()
        self.assertNotIn("圖表", self.bot.channels_state["4"]["board_fails"], "one update that works starts again")
        fail["on"] = True
        post(3)
        self.assertEqual(len(told), 2)
        self.assertEqual(posts.error_code(asyncio.TimeoutError()), "TimeoutError")
        self.assertIn("HTTP 403，錯誤碼 50013：Missing Permissions", said["text"])
        # Discord not reachable at all: the notification goes on to safely(), which keeps it to try again
        fail["on"], kept = False, []
        post()

        async def nowhere(post, sender_ch):
            raise posts.Undelivered(post)
        self.p.notify = nowhere
        fail["on"] = True
        post(2)
        with mock.patch.object(posts.postbox, "fail", lambda path, p: kept.append(p)), \
                contextlib.redirect_stdout(io.StringIO()):
            asyncio.run(self.p.safely({"channel": "dashboard", "board": "圖表", "text": "x"}, "spool/1.json"))
        (left,) = kept
        self.assertTrue(left["notify"])
        self.assertIn("「圖表」", left["text"])
        self.assertEqual(self.bot.channels_state["4"]["board_fails"]["圖表"], 3, "the board's own state was saved")

    def test_a_question_while_on_a_post_is_announced_where_the_post_came_from(self):
        edits = []

        class Partial(object):
            def __init__(self, mid):
                self.mid = mid

            async def edit(self, content=None):
                edits.append((self.mid, content))
        self.a.get_partial_message = Partial
        self.post(channel="st-0", text="幫我查一下", from_channel="1")
        self.bot.channels_state["1"]["busy"] = True
        asyncio.run(self.p.asking(self.b, "q1", 77))
        self.assertIn("<#2> 的 Claude 在等你回答問題", self.a.sent[-1])
        self.assertIn("/2/77", self.a.sent[-1])
        self.assertEqual(self.bot.tmux.given[-1][0], 1, "the busy sender is told not to wait")
        asyncio.run(self.p.answered(self.b, "q1", "已回答"))
        self.assertIn("已回答", edits[-1][1])
        before = len(self.a.sent)
        self.p.owner_spoke(2)  # the owner talks there: later questions are the owner's own business
        asyncio.run(self.p.asking(self.b, "q2", 78))
        self.assertEqual(len(self.a.sent), before)

    def test_cards_have_a_colour_per_sender(self):
        one, two = posts.cards("#st-1", "a")[0], posts.cards("#st-1", "b")[0]
        self.assertEqual(one.color, two.color)
        self.assertEqual(one.author.name, "📨 來自 #st-1")

    def test_long_text_is_split(self):
        self.post(channel="notes", text="x" * 9000)
        self.assertEqual(len(self.notes.sent), 3)
        self.assertTrue(all(len(s) <= 4096 + 40 for s in self.notes.sent))
        self.assertIn("（1/3）", self.notes.sent[0])

    def test_loop_guard(self):
        for i in range(posts.LOOP_LIMIT):
            self.post(channel="st-0" if i % 2 == 0 else "st-1", text="m%d" % i, from_channel="1" if i % 2 == 0 else "2")
        self.assertEqual(len(self.bot.tmux.given), posts.LOOP_LIMIT)
        self.post(channel="st-0", text="one too many", from_channel="1")
        self.assertEqual(len(self.bot.tmux.given), posts.LOOP_LIMIT, "held, not handed over")
        self.assertIn("⏸️", self.b.sent[-1])
        self.p.owner_spoke(2)
        self.post(channel="st-0", text="after the owner spoke", from_channel="1")
        self.assertEqual(len(self.bot.tmux.given), posts.LOOP_LIMIT + 1)

    def test_limits_can_be_raised_switched_off_or_lifted_for_a_pair(self):
        s = self.bot.channels_state
        self.assertEqual(posts.pair_limit(s, 1, 2), posts.LOOP_LIMIT)
        s["1"]["talk_limit"] = 50
        self.assertEqual(posts.pair_limit(s, 1, 2), 50, "the larger of the two")
        s["2"]["talk_limit"] = 0
        self.assertEqual(posts.pair_limit(s, 1, 2), 0, "either side off: no limit")
        del s["1"]["talk_limit"], s["2"]["talk_limit"]
        s["2"]["talk_free"] = [1]
        self.assertEqual((posts.pair_limit(s, 1, 2), posts.pair_limit(s, 2, 1), posts.pair_limit(s, 2, 3)),
                         (0, 0, posts.LOOP_LIMIT), "a freed pair only")
        for i in range(posts.LOOP_LIMIT + 5):
            self.post(channel="st-0", text="m%d" % i, from_channel="1")
        self.assertEqual(len(self.bot.tmux.given), posts.LOOP_LIMIT + 5)

    def test_the_installation_sets_the_limit_of_a_channel_that_sets_none(self):
        """Setting dcbot.talk_limit (asked at install), as messages.load() read it; a channel's own comes first."""
        s = self.bot.channels_state
        with mock.patch.dict(posts.messages.loaded, {"talk_limit": 3}):
            self.assertEqual(posts.pair_limit(s, 1, 2), 3)
            s["1"]["talk_limit"] = 5
            self.assertEqual(posts.pair_limit(s, 1, 2), 5, "a channel's own comes first")
            del s["1"]["talk_limit"]
            for i in range(3):
                self.post(channel="st-0" if i % 2 == 0 else "st-1", text="m%d" % i, from_channel="1" if i % 2 == 0 else "2")
            self.post(channel="st-1", text="one too many", from_channel="2")
            self.assertEqual(len(self.bot.tmux.given), 3, "held after the installation's 3")
        with mock.patch.dict(posts.messages.loaded, {"talk_limit": 0}):
            self.assertEqual(posts.pair_limit(s, 1, 2), 0, "0: no limit")
        with mock.patch.dict(posts.messages.loaded, {"talk_limit": None}):
            self.assertEqual(posts.pair_limit(s, 1, 2), posts.LOOP_LIMIT, "the installation says nothing")

    def hold_once(self):
        for i in range(posts.LOOP_LIMIT):
            self.post(channel="st-0" if i % 2 == 0 else "st-1", text="m%d" % i, from_channel="1" if i % 2 == 0 else "2")
        self.post(channel="st-0", text="one too many", from_channel="1")

    def test_a_hold_rings_the_owner_and_tells_the_busy_sender(self):
        from bot import config
        self.bot.channels_state["1"]["busy"] = True
        self.hold_once()
        self.assertTrue(self.b.sent[-1].startswith("⏸️ <@%d> #st-1 " % config.OWNER_ID), self.b.sent[-1])
        self.assertEqual(len(self.bot.tmux.given), posts.LOOP_LIMIT + 1, "the post itself is not handed over")
        cid, text = self.bot.tmux.given[-1]
        self.assertEqual(cid, 1, "the sender is told")
        self.assertIn("#st-0", text)
        self.assertIn("不要空等，也不要重送", text)

    def test_a_sender_not_in_a_turn_is_not_told(self):
        self.hold_once()
        self.assertIn("⏸️", self.b.sent[-1])
        self.assertEqual(len(self.bot.tmux.given), posts.LOOP_LIMIT, "nothing typed into an idle conversation")

    def test_tick_does_not_wait_for_delivery(self):
        """The loop that calls tick() is the one that reads Claude's receipt: tick must return at once."""
        import time

        class SlowTmux(object):
            async def submit(self, message, text):
                await asyncio.sleep(2)
        self.bot.tmux = SlowTmux()
        old = posts.drain
        posts.drain = lambda: [{"channel": "st-0", "text": "x", "from_channel": "1"}]
        try:
            async def go():
                t0 = time.time()
                await self.p.tick()
                return time.time() - t0
            self.assertLess(asyncio.run(go()), 0.5)
        finally:
            posts.drain = old

    def test_spool(self):
        with tempfile.TemporaryDirectory() as tmp:
            posts.write({"channel": "x", "text": "1"}, tmp)
            posts.write({"channel": "y", "text": "2"}, tmp)
            self.assertEqual([p["channel"] for p in posts.drain(tmp)], ["x", "y"])
            self.assertEqual(posts.drain(tmp), [])


class OwnChannel(unittest.TestCase):
    """C-73: a Claude posting into its own channel gets plain messages; files, several, on the last one."""
    def setUp(self):
        self.bot = Bot()
        self.a, self.b = Chan(1, "st-1"), Chan(2, "st-0")
        self.bot.chans = {1: self.a, 2: self.b}
        self.bot.guild.text_channels = [self.a, self.b]
        self.bot.channels_state = {"1": {"tmux": "dc-a"}, "2": {"tmux": "dc-b"}}
        self.p = posts.Posts(self.bot)

    def post(self, **kw):
        asyncio.run(self.p.handle(kw))

    @staticmethod
    def files(*names):
        import base64
        got = [{"name": n, "b64": base64.b64encode(n.encode()).decode()} for n in names]
        post = {"file_name": got[0]["name"], "file_b64": got[0]["b64"]}
        if got[1:]:
            post["more_files"] = got[1:]
        return post

    def test_text_and_pictures_as_plain_messages_pinging_nobody(self):
        self.post(channel="st-1", text="看圖 <@42> @everyone", from_channel="1", **self.files("a.png", "b.png"))
        self.assertEqual(self.a.sent, ["看圖 <@42> @everyone"], "no 📨 card")
        how, = self.a.how
        self.assertEqual((how["files"], how["embed"]), (["a.png", "b.png"], False))
        m = how["mentions"]
        self.assertEqual((m.everyone, m.users, m.roles, m.replied_user), (False, False, False, False))
        self.assertEqual(self.bot.tmux.given, [], "never handed back to its own Claude")

    def test_long_text_is_split_and_the_files_go_on_the_last(self):
        self.post(channel="st-1", text="\n".join("line %d %s" % (i, "x" * 60) for i in range(80)), from_channel="1",
                  **self.files("a.png"))
        self.assertGreater(len(self.a.sent), 1)
        self.assertTrue(all(len(t) <= 2000 for t in self.a.sent))
        self.assertEqual([h["files"] for h in self.a.how], [[]] * (len(self.a.sent) - 1) + [["a.png"]])

    def test_files_alone(self):
        self.post(channel="st-1", text="", from_channel="1", **self.files("a.png", "b.png", "c.png"))
        self.assertEqual(self.a.sent, [None])
        self.assertEqual(self.a.how[0]["files"], ["a.png", "b.png", "c.png"])

    def test_by_id_too(self):
        self.post(channel="1", text="hi", from_channel="1")
        self.assertEqual(self.a.sent, ["hi"])

    def test_into_another_channel_still_a_card_with_every_file_and_their_names_for_claude(self):
        self.post(channel="st-0", text="看圖", from_channel="1", **self.files("a.png", "b.png"))
        self.assertEqual(self.b.sent, ["📨 來自 #st-1\n看圖"])
        self.assertEqual(self.b.how[0]["files"], ["a.png", "b.png"])
        (cid, text), = self.bot.tmux.given
        self.assertIn("（附件：a.png、b.png，在 Discord 的這則訊息上）", text)

    def test_a_post_of_the_old_shape_one_file(self):
        import base64
        self.post(channel="st-0", text="x", from_channel="1", file_name="r.md", file_b64=base64.b64encode(b"#").decode())
        self.assertEqual(self.b.how[0]["files"], ["r.md"])


class Order(unittest.TestCase):
    """C-73: one sender's posts to one channel are shown in the order sent, each at once: handing them to that
    channel's Claude never holds them up; posts of others never wait for them."""
    def setUp(self):
        from bot import postbox
        self.spool = tempfile.mkdtemp()
        self.patch = mock.patch.object(postbox, "SPOOL", self.spool)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.bot = Bot()
        self.a, self.b, self.c = Chan(1, "st-1"), Chan(2, "st-0"), Chan(3, "st-2")
        self.bot.chans = {1: self.a, 2: self.b, 3: self.c}
        self.bot.guild.text_channels = [self.a, self.b, self.c]
        self.bot.channels_state = {"1": {"tmux": "dc-a"}, "2": {"tmux": "dc-b"}, "3": {"tmux": "dc-c"}}
        self.p = posts.Posts(self.bot)

    def write(self, **post):
        from bot import postbox
        postbox.write(post, self.spool)

    def run_ticks(self, secs):
        async def go():
            t0 = time.time()
            while time.time() - t0 < secs:
                await self.p.tick()
                await asyncio.sleep(0.02)
        asyncio.run(go())

    def test_a_big_picture_first_is_still_shown_first(self):
        import base64
        sends = []
        real = Chan.send

        async def send(ch, text=None, **kw):
            if kw.get("files"):
                await asyncio.sleep(0.3)  # the big one uploads slowly
            sends.append((ch.id, kw["embed"].description if kw.get("embed") else text))
            return await real(ch, text, **kw)
        big = base64.b64encode(b"x" * 1000).decode()
        self.write(channel="st-1", text="一", from_channel="1", file_name="big.png", file_b64=big)
        self.write(channel="st-1", text="二", from_channel="1")
        self.write(channel="st-2", text="別人的", from_channel="2")
        with mock.patch.object(Chan, "send", send):
            self.run_ticks(0.8)
        self.assertEqual([t for c, t in sends if c == 1], ["一", "二"])
        self.assertEqual(sends[0], (3, "別人的"), "another sender's post does not wait behind them")
        from bot import postbox
        self.assertEqual(postbox.waiting(self.spool), [])
        self.assertEqual(self.p.chains, {})

    def test_a_hand_over_that_takes_long_does_not_hold_what_is_shown(self):
        """Manager on the rehearsal: shown in order, at once; handing over is the channel's own queue."""
        stuck = asyncio.Event()
        given = []

        class Stuck(object):
            async def submit(self, message, text):
                given.append(text.rsplit("\n", 1)[-1])
                await stuck.wait()  # ssh to a machine that does not answer
        self.bot.tmux = Stuck()
        for t in ("一", "二", "三"):
            self.write(channel="st-0", text=t, from_channel="1")

        async def go():
            for _ in range(10):
                await self.p.tick()
                await asyncio.sleep(0.02)
            self.assertEqual(self.b.sent, ["📨 來自 #st-1\n一", "📨 來自 #st-1\n二", "📨 來自 #st-1\n三"])
            self.assertEqual(given, ["一", "二", "三"], "handed over in the order shown")
            from bot import postbox
            self.assertEqual(len(postbox.waiting(self.spool)), 3, "kept in the spool until handed over")
            stuck.set()
            await asyncio.sleep(0.05)
            self.assertEqual(postbox.waiting(self.spool), [])
        asyncio.run(go())

    def test_a_hand_over_that_breaks_is_kept_aside_and_said_where_it_came_from(self):
        class Broken(object):
            async def submit(self, message, text):
                raise AttributeError("'NoneType' object has no attribute 'replace'")
        self.bot.tmux = Broken()
        self.write(channel="st-0", text="一", from_channel="1")
        self.write(channel="st-0", text="from a script", from_name="cron")
        with contextlib.redirect_stderr(io.StringIO()):
            self.run_ticks(0.3)
        from bot import postbox
        self.assertEqual(postbox.waiting(self.spool), [])
        self.assertEqual(len(os.listdir(self.spool + "-bad")), 2, "not simply removed")
        self.assertEqual(self.b.sent, ["📨 來自 #st-1\n一", "📨 來自 cron\nfrom a script"], "shown all the same")
        said, = self.a.sent
        self.assertIn("這則沒交到 #st-0 的 Claude（AttributeError", said)
        self.assertIn("cron：⚠️ 這則沒交到 #st-0 的 Claude", self.bot.audits[0], "no channel of its own: #audit")
        self.assertEqual(self.p.inflight, set())

    def test_a_slow_post_is_said_once_with_its_parts_a_quick_one_not(self):
        from bot import postbox
        out = io.StringIO()
        old = os.path.join(self.spool, "%020d-1.json" % int((time.time() - 12) * 1e9))
        new = os.path.join(self.spool, "%020d-1.json" % time.time_ns())
        for path in (old, new):
            with open(path, "w") as f:
                f.write('{"channel": "st-1", "text": "x"}')
        with contextlib.redirect_stdout(out):
            asyncio.run(self.p.safely(postbox.waiting(self.spool)[0][1], old))
            asyncio.run(self.p.safely({"channel": "st-1", "text": "y"}, new))
        line, = out.getvalue().strip().splitlines()
        self.assertRegex(line, r"^\[posts\] #st-1：1\d\.\d 秒（排隊 1\d\.\d、處理 0\.\d）$")


class Dcctl(unittest.TestCase):
    def test_dcctl_post_runs_with_the_system_python(self):
        """dcctl is run by /usr/bin/python3 (no discord module): what `post` needs must import without it."""
        import json
        import subprocess
        with tempfile.TemporaryDirectory() as tmp:
            code = ("import sys; sys.path.insert(0, %r); from bot import config; config.STATE = %r; "
                    "from bot import postbox; postbox.SPOOL = %r; "
                    "postbox.write({'channel': 'x', 'text': 'hi'})") % (ROOT, tmp, os.path.join(tmp, "posts"))
            r = subprocess.run(["/usr/bin/python3", "-c", code], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            self.assertEqual(r.returncode, 0, r.stderr.decode())
            self.assertEqual(posts.drain(os.path.join(tmp, "posts")), [{"channel": "x", "text": "hi"}])
        r = subprocess.run(["/usr/bin/python3", os.path.join(ROOT, "tools", "dcctl"), "--help"], stdout=subprocess.PIPE)
        self.assertEqual(r.returncode, 0)
        self.assertIn(b"dcctl post", r.stdout)


class SenderLookup(unittest.TestCase):
    def test_sender_channel_by_pane_on_any_machine(self):
        path = os.path.join(ROOT, "tools", "dcctl")
        loader = importlib.machinery.SourceFileLoader("dcctl_s", path)
        dcctl = importlib.util.module_from_spec(importlib.util.spec_from_loader("dcctl_s", loader))
        loader.exec_module(dcctl)
        me = os.uname().nodename.split(".")[0]
        from altention import config as altconfig
        names = {"alices-macbook-pro": "mac", "hostb": "HostB", "hostc": "HostC", me.lower(): me}  # a made-up list:
        fake = mock.patch.object(altconfig, "name_of", lambda h: names.get((h or "").lower()))  # not this machine's
        with tempfile.TemporaryDirectory() as tmp, fake:
            old, dcctl.config.STATE = dcctl.config.STATE, tmp
            try:
                import json
                with open(os.path.join(tmp, "routes.json"), "w") as f:
                    json.dump({"/s|%1": {"channel": "11", "pid": ""}}, f)
                with open(os.path.join(tmp, "channels.json"), "w") as f:
                    json.dump({"22": {"pane": {"socket": "/s", "id": "%2", "host": "HostB"}}, "33": {"pane": {"socket": "/s", "id": "%3"}}}, f)
                self.assertEqual(dcctl.sender_channel("%s|/s|%%1" % me), "11", "this machine, attached")
                self.assertEqual(dcctl.sender_channel("/s|%1"), "11")
                self.assertEqual(dcctl.sender_channel("hostb|/s|%2"), "22", "another machine's pane")
                self.assertEqual(dcctl.machine_name("alices-MacBook-Pro"), "mac", "a Mac's own hostname")
                self.assertEqual(dcctl.machine_name("nowhere"), "nowhere")
                self.assertIsNone(dcctl.sender_channel("HostC|/s|%2"))
                self.assertEqual(dcctl.sender_channel("%s|/s|%%3" % me), "33", "a window the bot opened here")
                # #issues 25: a Mac's tmux names its socket /private/tmp/…, the bot wrote /tmp/… (a link there)
                with open(os.path.join(tmp, "channels.json"), "w") as f:
                    json.dump({"44": {"pane": {"socket": "/tmp/tmux-501/default", "id": "%8", "host": "mac"}},
                               "55": {"pane": {"socket": "/tmp/tmux-501/default", "id": "%9", "host": "HostB"}}}, f)
                self.assertEqual(dcctl.sender_channel("alices-MacBook-Pro|/private/tmp/tmux-501/default|%8"), "44")
                self.assertEqual(dcctl.sender_channel("alices-MacBook-Pro|/tmp/tmux-501/default|%8"), "44")
                self.assertIsNone(dcctl.sender_channel("alices-MacBook-Pro|/private/tmp/tmux-502/default|%8"), "another user's server")
                self.assertIsNone(dcctl.sender_channel("alices-MacBook-Pro|/private/tmp/tmux-501/default|%9"), "that pane is HostB's")
                real = os.path.join(tmp, "real")
                os.makedirs(real)
                os.symlink(real, os.path.join(tmp, "link"))
                with open(os.path.join(tmp, "routes.json"), "w") as f:
                    json.dump({"%s|%%1" % os.path.join(tmp, "link", "sock"): {"channel": "66", "pid": ""}}, f)
                self.assertEqual(dcctl.sender_channel("%s|%s|%%1" % (me, os.path.join(real, "sock"))), "66", "this machine: the same file")
                self.assertIsNone(dcctl.sender_channel("%s|%s|%%2" % (me, os.path.join(real, "sock"))))
            finally:
                dcctl.config.STATE = old


class Dcpost(unittest.TestCase):
    def test_command_line(self):
        path = os.path.join(os.path.dirname(ROOT), "altention", "dc", "post.py")
        loader = importlib.machinery.SourceFileLoader("dcpost", path)
        dcpost = importlib.util.module_from_spec(importlib.util.spec_from_loader("dcpost", loader))
        loader.exec_module(dcpost)
        p = dcpost.build(["#reports", "hello", "there"], "", {"DCBOT_CHANNEL": "55"}, "host-a", "/w/proj")
        self.assertEqual((p["channel"], p["text"], p["from_channel"], p["from_name"]), ("reports", "hello there", "55", "host-a 上的 Claude（proj）"))
        p = dcpost.build(["logs"], "from stdin\n", {"TMUX": "/s,1,0", "TMUX_PANE": "%7"}, "HostB", "/x")
        self.assertEqual((p["text"], p["pane"]), ("from stdin", "HostB|/s|%7"), "the machine goes along with the pane")
        with tempfile.NamedTemporaryFile(suffix=".md") as f:
            f.write(b"# report")
            f.flush()
            p = dcpost.build(["reports", "--file", f.name, "weekly"], "", {}, "h", "/x")
            self.assertEqual((p["text"], p["file_name"]), ("weekly", os.path.basename(f.name)))
        with tempfile.TemporaryDirectory() as tmp:
            names = []
            for i in range(11):
                names.append(os.path.join(tmp, "p%d.png" % i))
                with open(names[-1], "wb") as f:
                    f.write(b"%d" % i)
            p = dcpost.build(["reports", "--file", names[0], "--file", names[1], "--file", names[2], "圖"], "", {}, "h", "/x")
            self.assertEqual((p["text"], p["file_name"], [f["name"] for f in p["more_files"]]), ("圖", "p0.png", ["p1.png", "p2.png"]))
            argv = ["reports"] + [w for n in names[:10] for w in ("--file", n)]
            self.assertEqual(len(dcpost.build(argv, "", {}, "h", "/x")["more_files"]), 9, "ten is allowed")
            with self.assertRaises(SystemExit) as e:
                dcpost.build(argv + ["--file", names[10]], "", {}, "h", "/x")
            self.assertIn("最多 10 個", str(e.exception.code))
            with mock.patch.object(dcpost, "MAX_FILE", 3):  # together, not each
                dcpost.build(["reports", "--file", names[0], "--file", names[1]], "", {}, "h", "/x")
                with self.assertRaises(SystemExit) as e:
                    dcpost.build(["reports", "--file", names[0], "--file", names[1], "--file", names[2], "--file", names[3]], "", {}, "h", "/x")
                self.assertIn("全部加起來", str(e.exception.code))
            with self.assertRaises(SystemExit):
                dcpost.build(["reports", "x", "--file"], "", {}, "h", "/x")
            self.assertNotIn("more_files", dcpost.build(["reports", "--file", names[0]], "", {}, "h", "/x"), "one file: the old shape")
        p = dcpost.build(["行程", "--board", "本週", "- 週一"], "", {}, "h", "/x")
        self.assertEqual((p["board"], p["text"]), ("本週", "- 週一"))

    def test_file_with_text_from_stdin(self):
        """main(): text from stdin goes along with --file, as usage says (it was dropped: stdin was read only when the
        channel was all the command line held, support-2 2026-10-20)."""
        path = os.path.join(os.path.dirname(ROOT), "altention", "dc", "post.py")
        loader = importlib.machinery.SourceFileLoader("dcpost", path)
        dcpost = importlib.util.module_from_spec(importlib.util.spec_from_loader("dcpost", loader))
        loader.exec_module(dcpost)

        def run(argv, stdin):
            sent = []
            with mock.patch.object(dcpost, "deliver", lambda post: (sent.append(post), (0, ""))[1]), \
                    mock.patch("sys.argv", ["post.py"] + argv), mock.patch("sys.stdin", io.StringIO(stdin)), \
                    mock.patch.dict(os.environ, {"DCBOT_CHANNEL": "55"}):
                with self.assertRaises(SystemExit) as e:
                    dcpost.main()
            self.assertIn(e.exception.code, (0, None))
            return sent[0]

        with tempfile.NamedTemporaryFile(suffix=".md") as f:
            f.write(b"# report")
            f.flush()
            name = os.path.basename(f.name)
            p = run(["reports", "--file", f.name], "long text\nfrom stdin\n")
            self.assertEqual((p["text"], p["file_name"]), ("long text\nfrom stdin", name), "--file and stdin: both")
            p = run(["reports", "--file", f.name, "weekly"], "ignored\n")
            self.assertEqual((p["text"], p["file_name"]), ("weekly", name), "--file and text: the text given, stdin unread")
            p = run(["reports", "--file", f.name], "")
            self.assertEqual((p["text"], p["file_name"]), ("", name), "--file alone")
            self.assertTrue(p["file_b64"])
        p = run(["reports"], "only stdin\n")
        self.assertEqual(p["text"], "only stdin")
        p = run(["reports", "--history"], "not for it\n")
        self.assertEqual((p.get("history"), p["text"]), (True, ""), "--history does not read stdin")



class ServerMap(unittest.TestCase):
    def test_map_shows_categories_kinds_and_the_caller(self):
        path = os.path.join(ROOT, "tools", "dcctl")
        loader = importlib.machinery.SourceFileLoader("dcctl_m", path)
        dcctl = importlib.util.module_from_spec(importlib.util.spec_from_loader("dcctl_m", loader))
        loader.exec_module(dcctl)
        chans = [{"id": "1", "type": 4, "name": "日常", "position": 0}, {"id": "2", "type": 4, "name": "proj", "position": 1},
                 {"id": "10", "type": 0, "name": "控制台", "parent_id": "1", "position": 0, "topic": "交代事情"},
                 {"id": "11", "type": 0, "name": "reports", "parent_id": "1", "position": 1},
                 {"id": "20", "type": 0, "name": "run-0", "parent_id": "2", "position": 0},
                 {"id": "21", "type": 0, "name": "sh", "parent_id": "2", "position": 1},
                 {"id": "30", "type": 2, "name": "voice", "position": 0}]
        state = {"10": {"tmux": "dc-x", "cwd": "/w"}, "11": {"plain": True},
                 "20": {"pane": {"host": "HostB"}, "cwd": "/p"}, "21": {"kind": "terminal"}}
        text = dcctl.structure(chans, state, "20")
        self.assertIn("你在 #run-0（分類「proj」）", text)
        self.assertLess(text.index("【日常】"), text.index("【proj】"))
        self.assertIn("- #控制台 🤖 Claude 對話，工作目錄 /w　— 交代事情", text)
        self.assertIn("- #reports 📝", text)
        self.assertIn("（在 HostB）", text)
        self.assertIn("← 你在這裡", text.split("- #run-0")[1].split("\n")[0])
        self.assertIn("- #sh 💻", text)
        self.assertNotIn("voice", text)
        self.assertIn("找不到你所在的頻道", dcctl.structure(chans, state, "999"))
        self.assertNotIn("你在", dcctl.structure(chans, state))


if __name__ == "__main__":
    unittest.main()


class TargetCheck(unittest.TestCase):
    def test_dcctl_refuses_unknown_names_with_suggestions(self):
        import importlib.machinery
        import importlib.util
        import os as _os
        from unittest import mock as _mock
        path = _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))), "tools", "dcctl")
        loader = importlib.machinery.SourceFileLoader("dcctl_mod", path)
        mod = importlib.util.module_from_spec(importlib.util.spec_from_loader("dcctl_mod", loader))
        loader.exec_module(mod)
        chans = [{"id": "9", "type": 4, "name": "日常"}, {"id": "1", "type": 0, "name": "usage", "parent_id": "9"},
                 {"id": "2", "type": 0, "name": "秘書", "parent_id": "9"}]
        with _mock.patch.object(mod, "channels", return_value=chans):
            self.assertIsNone(mod.post_target_error({"channel": "秘書"}))
            self.assertIsNone(mod.post_target_error({"channel": "日常/usage"}))
            self.assertIsNone(mod.post_target_error({"channel": "123"}))
            self.assertIsNone(mod.post_target_error({"channel": "新的", "create": True}))
            err = mod.post_target_error({"channel": "usag"})
            self.assertIn("#usage", err)
            self.assertIn("--create", err)
            self.assertIsNotNone(mod.post_target_error({"channel": "別的/usage"}))


class SameNames(unittest.TestCase):
    def test_dcctl_lists_same_named_channels_instead_of_taking_the_first(self):
        import importlib.machinery
        import importlib.util
        import os as _os
        path = _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))), "tools", "dcctl")
        loader = importlib.machinery.SourceFileLoader("dcctl_same", path)
        mod = importlib.util.module_from_spec(importlib.util.spec_from_loader("dcctl_same", loader))
        loader.exec_module(mod)
        chans = [{"id": "8", "type": 4, "name": "系統"}, {"id": "9", "type": 4, "name": "project-b"},
                 {"id": "1", "type": 0, "name": "notifications", "parent_id": "8"},
                 {"id": "2", "type": 0, "name": "notifications", "parent_id": "9"},
                 {"id": "3", "type": 4, "name": "notes"}, {"id": "4", "type": 0, "name": "notes", "parent_id": "8"}]
        found, why = mod.resolve("notifications", chans)
        self.assertIsNone(found)
        self.assertIn("系統/notifications（ID 1）", why)
        self.assertIn("project-b/notifications（ID 2）", why)
        self.assertEqual(mod.resolve("project-b/notifications", chans)[0]["id"], "2")
        self.assertEqual(mod.resolve("notifications", chans, 0, "project-b")[0]["id"], "2", "the sender's category settles it")
        self.assertEqual(mod.resolve("notes", chans)[0]["id"], "4", "a channel wins over a category of the same name")
        self.assertEqual(mod.resolve("2", chans)[0]["id"], "2")
        with mock.patch.object(mod, "channels", return_value=chans):
            self.assertIn("有 2 個頻道", mod.post_target_error({"channel": "notifications", "notify": True}))
            self.assertIsNone(mod.post_target_error({"channel": "brand-new", "notify": True}), "a notification is not refused: the bot puts it in the default channel")


class Queue(unittest.TestCase):
    def test_posts_stay_in_the_spool_until_handled_and_are_not_taken_twice(self):
        from bot import postbox
        spool = tempfile.mkdtemp()
        postbox.write({"channel": "notes", "text": "a"}, spool)
        with mock.patch.object(postbox, "SPOOL", spool):
            bot = Bot()
            p = posts.Posts(bot)
            started = []

            async def slow(post):
                started.append(post["text"])
                await asyncio.sleep(0.05)
            p.handle = slow

            async def go():
                await p.tick()
                await p.tick()  # the first is still being handled
                self.assertEqual(len(postbox.waiting(spool)), 1, "kept until handled")
                await asyncio.sleep(0.1)
            asyncio.run(go())
            self.assertEqual(started, ["a"])
            self.assertEqual(postbox.waiting(spool), [])

    def test_events_wait_for_done(self):
        from bot import events
        spool = tempfile.mkdtemp()
        for i, body in enumerate(['{"channel": "1", "n": 1}', "not json", '{"no": "channel"}']):
            with open(os.path.join(spool, "%d.json" % i), "w") as f:
                f.write(body)
        got = events.waiting(spool)
        self.assertEqual([e["n"] for _, e in got], [1])
        self.assertEqual(len(os.listdir(spool)), 1, "junk is cleared, the event stays")
        events.done(got[0][0])
        self.assertEqual(os.listdir(spool), [])


class NotifyNeverRefused(unittest.TestCase):
    """The path problems are reported on must not depend on settings that may be the problem."""

    def load(self):
        import importlib.machinery
        import importlib.util
        path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools", "dcctl")
        loader = importlib.machinery.SourceFileLoader("dcctl_notify", path)
        mod = importlib.util.module_from_spec(importlib.util.spec_from_loader("dcctl_notify", loader))
        loader.exec_module(mod)
        return mod

    CHANS = [{"id": "8", "type": 4, "name": "系統"}, {"id": "9", "type": 4, "name": "project-b"},
             {"id": "1", "type": 0, "name": "notifications", "parent_id": "8", "position": 0},
             {"id": "2", "type": 0, "name": "notifications", "parent_id": "9", "position": 5}]

    def test_a_shared_name_goes_to_one_of_them_saying_so(self):
        mod = self.load()
        post = mod.notify_target({"channel": "notifications", "text": "sync failing", "notify": True}, self.CHANS)
        self.assertEqual(post["channel"], "1")
        self.assertTrue(post["text"].startswith("（有 2 個 #notifications，送到 系統/notifications）"))
        with mock.patch.object(mod, "channels", return_value=self.CHANS):
            self.assertIsNone(mod.post_target_error(post))
        other = mod.notify_target({"channel": "notifications", "text": "x"}, self.CHANS)
        self.assertEqual(other["channel"], "notifications", "only notifications are never refused")

    def test_no_channel_takes_the_bot_machines_setting_or_notifications_when_unreadable(self):
        mod = self.load()
        from altention import config as altconfig
        with mock.patch.object(altconfig, "setting", lambda k, d=None: "系統/notifications"):
            self.assertEqual(mod.notify_target({"text": "x", "notify": True}, self.CHANS)["channel"], "系統/notifications")
        def broken(*a, **k):
            raise altconfig.ConfigError("plugins.json is not JSON")
        with mock.patch.object(altconfig, "setting", broken):
            post = mod.notify_target({"text": "x", "notify": True}, self.CHANS)
        self.assertEqual(post["channel"], "1", "still delivered, to one of the shared names")


class NotifyOwnCategory(NotifyNeverRefused):
    """Without -c, a project channel's Claude reports to its own category's #notifications (2026-10-07: they went to
    系統/notifications since the default carries a category)."""

    def setting(self, value="系統/notifications"):
        from altention import config as altconfig
        return mock.patch.object(altconfig, "setting", lambda k, d=None: value)

    def test_a_sender_whose_category_has_one(self):
        mod = self.load()
        with self.setting():
            post = mod.notify_target({"text": "訓練跑完了", "notify": True}, self.CHANS, "project-b")
        self.assertEqual(post["channel"], "2")
        self.assertEqual(post["text"], "訓練跑完了", "no note: it is the one meant")

    def test_a_sender_whose_category_has_none(self):
        mod = self.load()
        chans = self.CHANS + [{"id": "7", "type": 4, "name": "project-c"}]
        with self.setting():
            post = mod.notify_target({"text": "x", "notify": True}, chans, "project-c")
        self.assertEqual(post["channel"], "系統/notifications", "the default")

    def test_no_sender_scripts_cron_and_the_systems_own_alerts(self):
        mod = self.load()
        with self.setting():
            self.assertEqual(mod.notify_target({"text": "x", "notify": True}, self.CHANS)["channel"], "系統/notifications")

    def test_settings_unreadable(self):
        mod = self.load()
        from altention import config as altconfig
        def broken(*a, **k):
            raise altconfig.ConfigError("plugins.json is not JSON")
        with mock.patch.object(altconfig, "setting", broken):
            self.assertEqual(mod.notify_target({"text": "x", "notify": True}, self.CHANS, "project-b")["channel"], "2")
            post = mod.notify_target({"text": "x", "notify": True}, self.CHANS)
        self.assertEqual(post["channel"], "1", "no sender: still delivered")

    def test_a_channel_named_with_c_is_left_as_it_is(self):
        mod = self.load()
        with self.setting():
            post = mod.notify_target({"channel": "系統/notifications", "text": "x", "notify": True}, self.CHANS, "project-b")
        self.assertEqual(post["channel"], "系統/notifications")


class PostWithoutTheChannelList(unittest.TestCase):
    """dcctl post must queue without asking Discord when it does not need to (IDs, or a notification when the list
    cannot be had): the scheduled posts and the reporting path keep working while Discord's API has trouble."""

    def run_post(self, post):
        import importlib.machinery
        import importlib.util
        import io
        import json
        import sys
        path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools", "dcctl")
        loader = importlib.machinery.SourceFileLoader("dcctl_q", path)
        mod = importlib.util.module_from_spec(importlib.util.spec_from_loader("dcctl_q", loader))
        loader.exec_module(mod)
        from bot import postbox
        written, asked = [], []

        def down():
            asked.append(1)
            if self.how == "exit":
                sys.exit("Discord API 錯誤 503")  # as call() does on an HTTP error
            raise OSError("connection refused")  # the network itself
        with mock.patch.object(mod, "channels", down), mock.patch.object(postbox, "write", written.append), \
                mock.patch.object(postbox, "heartbeat_age", return_value=1), \
                mock.patch.object(sys, "stdin", io.StringIO(json.dumps(post))), mock.patch("sys.stdout", io.StringIO()):
            mod.main(["post"])
        return written, asked

    def test_an_id_post_is_queued_without_the_list(self):
        written, asked = self.run_post({"channel": "100000000000000030", "text": "board"})
        self.assertEqual(len(written), 1)
        self.assertEqual(asked, [], "Discord not asked at all")

    how = "exit"

    def test_a_notification_is_queued_when_the_list_cannot_be_had(self):
        for how in ("exit", "network"):
            self.how = how
            for post in ({"text": "sync failing", "notify": True}, {"channel": "notifications", "text": "x", "notify": True}):
                written, asked = self.run_post(post)
                self.assertEqual(len(written), 1, (how, post))
                self.assertTrue(written[0]["channel"])


class FewerListCalls(unittest.TestCase):
    """C-73: Discord gives the whole bot 10 channel lists a minute; dcctl post asks at most once, and a 429 is said
    in words the sending Claude reads, not slept through unsaid."""

    def load(self):
        import importlib.machinery
        import importlib.util
        path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools", "dcctl")
        loader = importlib.machinery.SourceFileLoader("dcctl_f", path)
        mod = importlib.util.module_from_spec(importlib.util.spec_from_loader("dcctl_f", loader))
        loader.exec_module(mod)
        return mod

    def asked(self, post):
        import json
        import sys
        from bot import postbox
        mod, asked, written = self.load(), [], []
        chans = [{"id": "10", "type": 4, "name": "日常", "parent_id": None},
                 {"id": "11", "type": 0, "name": "st-1", "parent_id": "10"},
                 {"id": "12", "type": 0, "name": "notes", "parent_id": "10"}]
        with mock.patch.object(mod, "channels", lambda: (asked.append(1), chans)[1]), \
                mock.patch.object(postbox, "write", written.append), mock.patch.object(postbox, "heartbeat_age", return_value=1), \
                mock.patch.object(sys, "stdin", io.StringIO(json.dumps(post))), mock.patch("sys.stdout", io.StringIO()):
            mod.main(["post"])
        self.assertEqual(len(written), 1)
        return len(asked)

    def test_once_for_a_name_never_for_an_id(self):
        self.assertEqual(self.asked({"channel": "notes", "text": "x", "from_channel": "11"}), 1, "it was twice")
        self.assertEqual(self.asked({"channel": "notes", "text": "x"}), 1)
        self.assertEqual(self.asked({"channel": "12", "text": "x", "from_channel": "11"}), 0, "an ID from a channel: none")
        self.assertEqual(self.asked({"channel": "11", "text": "own", "from_channel": "11"}), 0)
        self.assertEqual(self.asked({"channel": "notes", "text": "x", "notify": True, "from_channel": "11"}), 1)
        self.assertEqual(self.asked({"channel": "日常/新的", "text": "x", "create": True, "from_channel": "11"}), 1)

    def test_a_429_is_said_and_waited_for(self):
        import json
        import urllib.error
        mod = self.load()
        tries = []

        class Answer(object):
            def __init__(self, body):
                self.body = body

            def read(self):
                return self.body

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        def urlopen(req, timeout=None):
            tries.append(req.get_method())
            if len(tries) == 1:
                raise urllib.error.HTTPError(req.full_url, 429, "Too Many Requests", {},
                                             io.BytesIO(json.dumps({"retry_after": 0.01}).encode()))
            return Answer(b"[]")
        err = io.StringIO()
        with mock.patch.object(mod.urllib.request, "urlopen", urlopen), mock.patch.object(mod.time, "sleep", lambda s: None), \
                contextlib.redirect_stderr(err):
            self.assertEqual(mod.call("GET", "/guilds/5/channels"), [])
            tries.clear()
            mod.call("GET", "/guilds/5/channels")
        first, second = err.getvalue().splitlines()
        self.assertEqual(first, "Discord 限速中（GET /guilds/5/channels），等 0 秒後再試", "nothing said of resending yet")
        self.assertEqual(second, first)

    def test_429s_that_do_not_end_say_it_did_not_go(self):
        import json
        import sys
        import urllib.error
        from bot import postbox
        mod, written = self.load(), []

        def urlopen(req, timeout=None):
            raise urllib.error.HTTPError(req.full_url, 429, "Too Many Requests", {}, io.BytesIO(b'{"retry_after": 0.01}'))
        err, out = io.StringIO(), io.StringIO()
        with mock.patch.object(mod.urllib.request, "urlopen", urlopen), mock.patch.object(mod.time, "sleep", lambda s: None), \
                mock.patch.object(postbox, "write", written.append), contextlib.redirect_stderr(err), contextlib.redirect_stdout(out), \
                mock.patch.object(sys, "stdin", io.StringIO(json.dumps({"channel": "notes", "text": "x"}))):
            with self.assertRaises(SystemExit) as e:
                mod.main(["post"])
        self.assertEqual(e.exception.code, "Discord 限速，這則沒有送出，等一下再送")
        self.assertEqual((written, out.getvalue()), ([], ""), "nothing queued, nothing said to have gone")
        self.assertEqual(len(err.getvalue().splitlines()), 4)
        self.assertNotIn("不用重送", err.getvalue())
        with mock.patch.object(mod.urllib.request, "urlopen", urlopen), mock.patch.object(mod.time, "sleep", lambda s: None), \
                contextlib.redirect_stderr(io.StringIO()):
            mod.GAVE_UP = self.load().GAVE_UP
            with self.assertRaises(SystemExit) as e:
                mod.call("GET", "/guilds/5/channels")
        self.assertEqual(e.exception.code, "Discord 限速中（GET /guilds/5/channels），試了 5 次都沒過，等一下再試")


class DiscordWarnings(unittest.TestCase):
    """C-73: discord.py's warnings (a 429 and its retry_after) reach supervisor.out; its info lines do not."""
    def test_warnings_only_once_each(self):
        import logging
        from bot import main
        log = logging.getLogger("discord")
        before = list(log.handlers), log.level, log.propagate
        out = io.StringIO()
        try:
            main.discord_warnings(out)
            main.discord_warnings(out)  # twice: still one line each
            logging.getLogger("discord.http").warning("We are being rate limited. %s %s responded with 429. Retrying in %.2f seconds.",
                                                      "PATCH", "https://discord.com/api/v10/channels/5/messages/6", 2.5)
            logging.getLogger("discord.client").info("logging in using static token")
        finally:
            log.handlers[:], log.level, log.propagate = before[0], before[1], before[2]
        line, = out.getvalue().splitlines()
        self.assertRegex(line, r"^\[discord\] \d{4}-\d\d-\d\d \d\d:\d\d:\d\d discord\.http: We are being rate limited\. PATCH .* Retrying in 2\.50 seconds\.$")


class KeptAndTriedAgain(unittest.TestCase):
    """A notification that could be shown nowhere stays in state/posts-failed/ and is tried again (manager
    2026-10-11): its spool file is not simply removed as done."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.spool, self.failed = os.path.join(self.tmp, "posts"), os.path.join(self.tmp, "posts-failed")
        self.bot = Bot()
        self.a, self.notes = Chan(1, "st-0"), Chan(3, "notes")
        self.bot.chans = {1: self.a, 3: self.notes}
        self.bot.guild.text_channels = [self.a, self.notes]
        self.p = posts.Posts(self.bot)
        self.patches = [mock.patch.object(posts.postbox, "SPOOL", self.spool), mock.patch.object(posts.postbox, "FAILED", self.failed),
                        mock.patch.object(posts, "notify_default", return_value="notes")]
        for p in self.patches:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in self.patches])

    def down(self, yes=True):
        for c in (self.a, self.notes, self.bot.audit_ch):
            c.fails = OSError("Cannot connect to host discord.com") if yes else None

    def tick(self):
        async def go():
            await self.p.tick()
            for _ in range(20):
                await asyncio.sleep(0)
        with contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
            asyncio.run(go())

    def kept(self):
        return [post for _, post in posts.postbox.waiting(self.failed)]

    def test_kept_then_shown_once_discord_is_back(self):
        from bot import postbox
        self.down()
        postbox.write({"channel": "st-0", "text": "disk full", "notify": True, "from_name": "HostC"}, self.spool)
        postbox.write({"channel": "st-0", "text": "an ordinary post"}, self.spool)
        self.tick()
        self.assertEqual(os.listdir(self.spool), [], "out of the spool: not handled again every half second")
        kept = self.kept()
        self.assertEqual([(k["text"], k["channel"]) for k in kept], [("disk full", "st-0")], "the notification only: an ordinary post is as before")
        self.assertTrue(kept[0]["failed_at"])
        self.tick()  # within RETRY_EVERY: not tried again yet
        self.assertEqual(len(self.kept()), 1)
        self.down(False)
        self.p.retried = 0
        self.tick()
        self.assertEqual(self.a.sent, ["🔔 **[HostC]** disk full"])
        self.assertEqual(self.kept(), [])

    def test_a_restart_tries_at_once_and_a_day_is_the_end(self):
        from bot import postbox
        postbox.fail(None, {"channel": "st-0", "text": "fresh", "notify": True, "from_name": "H"}, self.failed)
        postbox.fail(None, {"channel": "st-0", "text": "old", "notify": True, "from_name": "H", "failed_at": time.time() - 25 * 3600}, self.failed)
        postbox.fail(None, {"channel": "st-0", "text": "odd", "notify": True, "from_name": "H", "failed_at": "yesterday"}, self.failed)
        self.p = posts.Posts(self.bot)  # as after a restart
        self.tick()
        self.assertEqual(self.a.sent, ["🔔 **[H]** fresh"])
        self.assertEqual(sorted(k["text"] for k in self.kept()), ["odd", "old"], "kept for the owner to read; doctor says so")

    def test_still_down_keeps_what_is_left_and_when_it_first_failed(self):
        from bot import postbox
        long = "\n".join("line %03d %s" % (i, "x" * 60) for i in range(60))
        postbox.fail(None, {"channel": "st-0", "text": long, "notify": True, "from_name": "H", "failed_at": time.time() - 3600}, self.failed)
        first = self.kept()[0]["failed_at"]
        self.a.fails, self.notes.fails, self.bot.audit_ch.fails = 1, OSError("503"), OSError("503")
        self.tick()
        kept = self.kept()
        self.assertEqual((len(kept), kept[0]["failed_at"], kept[0]["continued"]), (1, first, True))
        self.assertLess(len(kept[0]["text"]), len(long))
        self.a.fails = None
        self.p.retried = 0
        self.tick()
        rows = LINE.findall("\n".join(self.a.sent))
        self.assertEqual("\n".join(rows), long, "every line once")
        self.assertIn("（接續先前沒送完的通知）", self.a.sent[1])
        self.assertEqual(self.kept(), [])

    def test_a_slow_round_is_not_overtaken_by_the_next(self):
        """#code-review 2026-10-11: with A held up in a slow channel past RETRY_EVERY, the next round showed B and
        removed it, and the first round then showed B again from the list it had read."""
        from bot import postbox
        slow = Chan(7, "slow")
        self.bot.chans[7] = slow
        self.bot.guild.text_channels.append(slow)
        real = slow.send

        async def takes_long(text=None, **kw):
            await asyncio.sleep(0.15)
            return await real(text, **kw)
        slow.send = takes_long
        postbox.fail(None, {"channel": "slow", "text": "A", "notify": True, "from_name": "H"}, self.failed)
        postbox.fail(None, {"channel": "st-0", "text": "B", "notify": True, "from_name": "H"}, self.failed)

        async def go():
            with mock.patch.object(postbox, "RETRY_EVERY", 0.02):
                for _ in range(30):
                    await self.p.tick()
                    await asyncio.sleep(0.02)
        asyncio.run(go())
        self.assertEqual((slow.sent, self.a.sent), (["🔔 **[H]** A"], ["🔔 **[H]** B"]), "each once")
        self.assertEqual(self.kept(), [])

    def test_a_file_gone_meanwhile_is_not_shown(self):
        from bot import postbox
        postbox.fail(None, {"channel": "st-0", "text": "B", "notify": True, "from_name": "H"}, self.failed)
        listed = postbox.failed(self.failed)
        os.remove(listed[0][0])
        with mock.patch.object(postbox, "failed", lambda: listed):
            asyncio.run(self.p.retry())
        self.assertEqual(self.a.sent, [])

    def test_what_notify_did_not_foresee_and_cannot_be_kept_stays_in_the_spool(self):
        """#code-review 2026-10-11: the spool file was removed as done, with nothing kept and nothing said."""
        from bot import postbox
        postbox.write({"channel": "st-0", "text": 5, "notify": True, "from_name": "H"}, self.spool)

        def full(*a, **k):
            raise OSError(28, "No space left on device")
        with mock.patch.object(postbox, "fail", full):
            self.tick()
            self.tick()
        self.assertEqual(len(os.listdir(self.spool)), 1, "still there for the bot's next start")
        self.assertEqual(self.a.sent, [])

    def test_only_the_newest_are_kept(self):
        from bot import postbox
        with mock.patch.object(postbox, "FAILED_KEEP", 3):
            for i in range(5):
                postbox.fail(None, {"text": str(i), "notify": True}, self.failed)
        self.assertEqual([k["text"] for k in self.kept()], ["2", "3", "4"])


class NotifySaysWhereItGoes(unittest.TestCase):
    """dcctl post, a notification for a channel that is not there: queued all the same (the bot puts it in the
    default channel), and the sender is told so instead of "sent to #that-name" (#issues 7)."""
    CHANS = [{"id": "1", "name": "系統", "type": 4}, {"id": "2", "name": "notifications", "type": 0, "parent_id": "1"},
             {"id": "3", "name": "st-0", "type": 0}]

    def said(self, post, default="系統/notifications"):
        import json
        import sys
        path = os.path.join(ROOT, "tools", "dcctl")
        loader = importlib.machinery.SourceFileLoader("dcctl_moved", path)
        mod = importlib.util.module_from_spec(importlib.util.spec_from_loader("dcctl_moved", loader))
        loader.exec_module(mod)
        from bot import postbox
        written, out = [], io.StringIO()
        with mock.patch.object(mod, "channels", lambda: self.CHANS), mock.patch.object(postbox, "write", written.append), \
                mock.patch.object(postbox, "heartbeat_age", return_value=1), mock.patch.object(mod, "notify_default", lambda: default), \
                mock.patch.object(sys, "stdin", io.StringIO(json.dumps(post))), mock.patch("sys.stdout", out):
            mod.main(["post"])
        return written, out.getvalue().strip()

    def test_a_name_that_is_not_there(self):
        written, said = self.said({"channel": "exp-alerts", "text": "x", "notify": True})
        self.assertEqual([w["channel"] for w in written], ["exp-alerts"], "queued as named: the bot decides where it lands")
        self.assertEqual(said, "（找不到頻道 #exp-alerts：通知會改送到預設的通知頻道 系統/notifications，開頭註明原本要送哪裡）")

    def test_a_name_that_is_there_and_no_name(self):
        for post in ({"channel": "st-0", "text": "x", "notify": True}, {"text": "x", "notify": True}):
            written, said = self.said(post)
            self.assertEqual(len(written), 1)
            self.assertTrue(said.startswith("已送出到 #"), said)

    def test_the_default_itself_not_there(self):
        written, said = self.said({"text": "x", "notify": True}, default="alerts")
        self.assertEqual(len(written), 1)
        self.assertEqual(said, "（預設的通知頻道 alerts 不存在：機器人會建立它再貼出）")


class GuideSettings(unittest.TestCase):
    """dcctl guide names the setting whose value is not text (the wish of 2026-10-06)."""

    def test_a_value_that_is_not_text_is_named(self):
        import importlib.machinery
        import importlib.util
        path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools", "dcctl")
        loader = importlib.machinery.SourceFileLoader("dcctl_guide", path)
        mod = importlib.util.module_from_spec(importlib.util.spec_from_loader("dcctl_guide", loader))
        loader.exec_module(mod)
        values = {"bot_machine": "host-a", "example_board": 3, "skill_examples": ["/a", "/b"]}
        with self.assertRaises(mod.GuideError) as e:
            mod.check_values(values)
        text = str(e.exception)
        self.assertIn("example_board（core.guide.example_board）是 int：3", text)
        self.assertIn("skill_examples（core.guide.skill_examples）是 list", text)
        self.assertNotIn("bot_machine", text)
        mod.check_values({"bot_machine": "host-a"})  # all text: fine
        with mock.patch.object(mod, "guide_values", return_value=({"bot_machine": "host-a"}, {})), \
                mock.patch("builtins.open", mock.mock_open(read_data="{{nope}}")):
            with self.assertRaises(mod.GuideError) as e:
                mod.guide_text()
        self.assertIn("{{nope}}", str(e.exception))
        from altention import config as altconfig
        with mock.patch.object(altconfig, "setting", lambda k, d=None: "Mac" if k == "core.guide.machine_titles" else d):
            with self.assertRaises(mod.GuideError) as e:
                mod.guide_values()
        self.assertIn("core.guide.machine_titles", str(e.exception))
