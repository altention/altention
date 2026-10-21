"""/archive (C-63): every kind of channel put away and taken back where it was; the archive's categories, 50 channels
each; the note at the start of the topic; /detach remembers where it was too."""
import asyncio
import time
import unittest
from unittest import mock

import discord

from bot import archive, config, kinds, layout, routes, tmuxio
from tests import tmuxguard  # noqa: F401,E402  (no test reaches a real tmux server)

P = {"socket": "/s", "id": "%5", "pid": "100"}


def run(coro):
    return asyncio.run(coro)


class Cat(object):
    def __init__(self, guild, name, cid):
        self.guild, self.name, self.id = guild, name, cid
        self.position = len(guild.categories)

    @property
    def channels(self):
        return [c for c in self.guild.text_channels if c.category is self]

    text_channels = channels

    async def edit(self, position=None):
        """As discord.py's _move: in before the first whose position is at least the one asked for."""
        others = sorted((c for c in self.guild.categories if c is not self), key=lambda c: c.position)
        index = next((i for i, c in enumerate(others) if c.position >= position), len(others))
        others.insert(index, self)
        for i, c in enumerate(others):
            c.position = i


class Ch(object):
    def __init__(self, guild, name, category=None, topic=None):
        self.guild, self.name, self.category, self.topic = guild, name, category, topic
        self.id = 900 + len(guild.text_channels)
        self.sent, self.edits = [], []
        guild.text_channels.append(self)

    async def edit(self, **kw):
        self.edits.append(kw)
        if "category" in kw:
            self.category = kw["category"]
        if "topic" in kw:
            self.topic = kw["topic"]

    async def send(self, text=None, view=None, **kw):
        self.sent.append(text)


class Guild(object):
    def __init__(self):
        self.categories, self.text_channels = [], []

    async def create_category(self, name):
        cat = Cat(self, name, 500 + len(self.categories))
        self.categories.append(cat)
        return cat

    def cat(self, name):
        return run(self.create_category(name))

    def order(self):
        return [c.name for c in sorted(self.categories, key=lambda c: c.position)]


class Tmux(object):
    def __init__(self, bot):
        self.bot, self.closed, self.started = bot, [], []

    async def close(self, ch, kill=True):
        """As TmuxBridge.close: a window the bot opened (home and pane) is killed, its pane taken off the state."""
        st = self.bot.chan(ch)
        self.closed.append((ch.id, kill))
        if kill and st.get("home") and st.get("pane"):
            st.pop("pane")

    async def start_now(self, ch):
        self.started.append(ch.id)
        return True


class Bot(object):
    def __init__(self):
        self.channels_state, self.audits, self.saves = {}, [], 0
        self.tmux, self.terminal = Tmux(self), type("T", (), {"running": {}})()

    def chan(self, ch):
        return self.channels_state.setdefault(str(ch.id), {"cwd": "/w"})

    def save(self):
        self.saves += 1

    async def audit(self, text):
        self.audits.append(text)


class Base(unittest.TestCase):
    def setUp(self):
        self.g = Guild()
        self.bot = Bot()
        self.arch = archive.Archive(self.bot)
        self.unbound, self.killed = [], []

        async def unbind_any(cid, host=None):
            self.unbound.append(cid)

        async def exists(pane):
            return True

        async def kill_pane(pane):
            self.killed.append(pane["id"])
        for p in (mock.patch.object(routes, "unbind_any", unbind_any), mock.patch.object(tmuxio, "exists", exists),
                  mock.patch.object(tmuxio, "kill_pane", kill_pane),
                  mock.patch.object(tmuxio, "label", lambda pane: "lap:%s" % pane.get("id")),
                  mock.patch.object(config, "OWNER_ID", 111)):
            p.start()
            self.addCleanup(p.stop)
        self.proj = self.g.cat("proj")

    def put(self, ch, force=False):
        return run(self.arch.put(ch, force))

    def state(self, ch):
        return self.bot.channels_state[str(ch.id)]


class Click(object):
    type = discord.InteractionType.component

    def __init__(self, ch, what):
        self.channel, self.data = ch, {"custom_id": "arch:%d:%s" % (ch.id, what)}
        self.user = type("U", (), {"id": 111})()
        self.response = type("R", (), {})()
        self.edited = []

        async def edit_message(content=None, view=None):
            self.edited.append(content)

        async def send_message(text, view=None, ephemeral=False):
            self.edited.append(text)
        self.response.edit_message, self.response.send_message = edit_message, send_message


class Conversations(Base):
    def test_a_window_the_bot_opened_is_closed_and_the_conversation_kept(self):
        ch = Ch(self.g, "work", self.proj, "做研究")
        home = {"socket": "/u", "session": "main", "window": ""}
        self.bot.channels_state[str(ch.id)] = {"cwd": "/w", "home": home, "pane": P, "session_id": "sid",
                                               "permission": "auto", "boards": {"b": [1]}, "turns": 3}
        text, view = self.put(ch)
        self.assertIsNone(view)
        self.assertEqual(self.bot.tmux.closed, [(ch.id, True)], "closed, as the idle reaper does")
        st = self.state(ch)
        rec = st["archived"]
        self.assertEqual((rec["session_id"], rec["home"], rec["pane"], rec["why"]), ("sid", home, P, kinds.BY_OWNER))
        self.assertEqual(rec["from"], {"id": self.proj.id, "name": "proj"})
        self.assertEqual(rec["kept"], {"permission": "auto"})
        self.assertEqual(set(st), {"archived", "cwd", "boards"}, "a board in it is still edited in place")
        self.assertEqual(ch.category.name, config.ARCHIVE_CATEGORY)
        self.assertTrue(ch.topic.startswith("📦 原分類：proj · 封存於 %s\n做研究" % time.strftime("%Y-%m-%d")), ch.topic)
        self.assertEqual(len(ch.edits), 1, "category and topic in one edit (Discord's limit on topic changes)")
        self.assertIn("Claude 的視窗關掉了，對話留著", text)
        self.assertEqual(kinds.channel_kind(st), kinds.ARCHIVED)

    def test_an_attached_pane_is_let_go_of_not_closed(self):
        ch = Ch(self.g, "mine", self.proj)
        self.bot.channels_state[str(ch.id)] = {"cwd": "/w", "pane": dict(P, host="lap"), "pane_session": "s", "session_id": "sid"}
        text, _ = self.put(ch)
        self.assertEqual(self.bot.tmux.closed, [(ch.id, False)])
        self.assertEqual(self.unbound, [ch.id])
        self.assertEqual(self.state(ch)["archived"]["pane"]["host"], "lap")
        self.assertIn("你接上的對話還在原處繼續跑（lap:%5）", text)

    def test_no_window_only_a_session_id(self):
        """The 11 of the owner's list that /detach refused: the window closed by the idle reaper."""
        ch = Ch(self.g, "naming", self.proj)
        self.bot.channels_state[str(ch.id)] = {"cwd": "/w", "tmux": "dc-naming-1", "session_id": "sid", "busy": False}
        self.put(ch)
        rec = self.state(ch)["archived"]
        self.assertEqual((rec["tmux"], rec["session_id"]), ("dc-naming-1", "sid"))
        self.assertEqual(ch.category.name, config.ARCHIVE_CATEGORY)

    def test_at_work_asks_first(self):
        ch = Ch(self.g, "busy", self.proj)
        self.bot.channels_state[str(ch.id)] = {"cwd": "/w", "tmux": "dc-b", "session_id": "sid", "busy": True}
        text, view = self.put(ch)
        self.assertIn("正在工作", text)
        self.assertEqual([b.custom_id for b in view.children], ["arch:%d:force" % ch.id])
        self.assertNotIn("archived", self.state(ch), "nothing done before the owner says so")
        self.assertEqual((self.bot.tmux.closed, ch.category), ([], self.proj))
        c = Click(ch, "force")
        run(self.arch.on_interaction(c))
        self.assertIn("archived", self.state(ch))
        self.assertIn("📦 已封存", ch.sent[-1])

    def test_taken_back_on_the_bots_own_server(self):
        """A session of the bot's own server: opened again there (no home in the owner's tmux), resuming."""
        ch = Ch(self.g, "naming", self.proj, "t")
        self.bot.channels_state[str(ch.id)] = {"cwd": "/w", "tmux": "dc-naming-1", "session_id": "sid", "talk_limit": 5}
        self.put(ch)

        async def standing(pane):
            return "gone"
        with mock.patch.object(tmuxio, "standing", standing):
            run(self.arch.on_interaction(Click(ch, "back")))
        st = self.state(ch)
        self.assertEqual((st.get("session_id"), st.get("home"), st.get("talk_limit")), ("sid", None, 5))
        self.assertNotIn("archived", st)
        self.assertEqual(self.bot.tmux.started, [ch.id])
        self.assertIs(ch.category, self.proj, "back where it was")
        self.assertEqual(ch.topic, "t", "the note taken off")


class OtherKinds(Base):
    def test_a_record_channel_keeps_its_state_and_comes_back_as_it_was(self):
        ch = Ch(self.g, "logs", self.proj, "紀錄")
        before = {"cwd": "/w", "plain": True, "configured": True, "boards": {"週": [1, 2]}}
        self.bot.channels_state[str(ch.id)] = dict(before)
        text, _ = self.put(ch)
        st = self.state(ch)
        self.assertEqual(st["archived"]["kind"], kinds.RECORD)
        self.assertEqual(dict((k, v) for k, v in st.items() if k != "archived"), before)
        self.assertIn("照樣會貼進來", text)
        self.assertEqual(self.bot.tmux.closed, [])
        run(self.arch.offer(ch))
        self.assertIn("要恢復嗎", ch.sent[-1])
        c = Click(ch, "restore")
        run(self.arch.on_interaction(c))
        self.assertEqual(self.state(ch), before)
        self.assertIs(ch.category, self.proj)
        self.assertEqual(ch.topic, "紀錄")
        self.assertIn("已恢復，頻道搬回「proj」", ch.sent[-1])

    def test_a_terminal_closes_its_shell_and_opens_one_again_later(self):
        ch = Ch(self.g, "sh", self.proj)
        home = {"socket": "/u", "session": "main", "window": ""}
        self.bot.channels_state[str(ch.id)] = {"cwd": "/w", "kind": "terminal", "home": home, "pane": dict(P, shell=True),
                                               "history": ["ls"]}
        text, _ = self.put(ch)
        st = self.state(ch)
        self.assertEqual(self.killed, ["%5"])
        self.assertNotIn("pane", st)
        self.assertEqual((st["home"], st["history"], st["archived"]["kind"]), (home, ["ls"], kinds.TERMINAL))
        self.assertIn("shell 視窗關掉了", text)
        run(self.arch.on_interaction(Click(ch, "restore")))
        self.assertEqual(self.state(ch), {"cwd": "/w", "kind": "terminal", "home": home, "history": ["ls"]},
                         "its next command opens a shell (Terminal.ensure)")

    def test_a_terminal_with_a_command_running_is_not_archived(self):
        ch = Ch(self.g, "sh", self.proj)
        self.bot.channels_state[str(ch.id)] = {"cwd": "/w", "kind": "terminal", "home": {}, "pane": dict(P, shell=True)}
        self.bot.terminal.running[ch.id] = {}
        text, view = self.put(ch)
        self.assertIn("有指令在跑", text)
        self.assertEqual((self.killed, ch.category), ([], self.proj))

    def test_a_files_channel(self):
        ch = Ch(self.g, "files", None)
        self.bot.channels_state[str(ch.id)] = {"cwd": "/w", "kind": "files", "files": {"root": "/w"}}
        self.put(ch)
        self.assertIsNone(self.state(ch)["archived"]["from"])
        self.assertTrue(ch.topic.startswith("📦 原分類：（沒有分類）"))
        run(self.arch.on_interaction(Click(ch, "restore")))
        self.assertIsNone(ch.category, "back in the main area, where it was")

    def test_the_bots_own_channels_and_archived_ones_are_refused(self):
        system = self.g.cat(config.SYSTEM_CATEGORY)
        for ch in (Ch(self.g, "x", system), Ch(self.g, config.AUDIT_CHANNEL), Ch(self.g, config.SETUP_CHANNEL)):
            text, _ = self.put(ch)
            self.assertIn("不能封存", text)
            self.assertIsNot(getattr(ch.category, "name", None), config.ARCHIVE_CATEGORY)
        ch = Ch(self.g, "logs", self.proj)
        self.bot.channels_state[str(ch.id)] = {"cwd": "/w", "plain": True}
        self.put(ch)
        self.assertIn("已經封存", self.put(ch)[0])


class Shelves(Base):
    def test_fifty_to_a_category_then_the_next_one_after_it(self):
        sys_cat = self.g.cat(config.SYSTEM_CATEGORY)  # last: kept last
        first = self.g.cat(config.ARCHIVE_CATEGORY)
        run(first.edit(position=sys_cat.position))  # (above the system category)
        for i in range(archive.SHELF_MAX):
            Ch(self.g, "old-%d" % i, first)
        ch = Ch(self.g, "logs", self.proj)
        self.bot.channels_state[str(ch.id)] = {"cwd": "/w", "plain": True}
        self.put(ch)
        self.assertEqual(ch.category.name, "%s 2" % config.ARCHIVE_CATEGORY)
        self.assertEqual(self.g.order(), ["proj", config.ARCHIVE_CATEGORY, "%s 2" % config.ARCHIVE_CATEGORY, config.SYSTEM_CATEGORY])
        old = first.channels[0]
        run(old.edit(category=self.proj))  # one taken out: room in the first again
        ch2 = Ch(self.g, "more", self.proj)
        self.bot.channels_state[str(ch2.id)] = {"cwd": "/w", "plain": True}
        self.put(ch2)
        self.assertIs(ch2.category, first)

    def test_names(self):
        a = config.ARCHIVE_CATEGORY
        self.assertEqual([archive.shelf_number(n) for n in (a, a + " 2", a + " 12", "archive", a + " 1", a + "2", "proj")],
                         [1, 2, 12, 1, None, None, None])


class TakenBack(Base):
    def archived(self, cat, topic="t"):
        ch = Ch(self.g, "logs", cat, topic)
        self.bot.channels_state[str(ch.id)] = {"cwd": "/w/repo", "plain": True}
        self.put(ch)
        return ch

    def test_its_category_deleted_and_made_again_by_name(self):
        ch = self.archived(self.proj)
        self.g.categories.remove(self.proj)
        again = self.g.cat("proj")
        run(self.arch.on_interaction(Click(ch, "restore")))
        self.assertIs(ch.category, again)

    def test_its_category_gone_goes_by_the_folder(self):
        ch = self.archived(self.proj)
        self.g.categories.remove(self.proj)
        with mock.patch.object(routes, "project_name", lambda cwd: "repo"):
            run(self.arch.on_interaction(Click(ch, "restore")))
        self.assertEqual(ch.category.name, "repo")

    def test_a_long_topic_is_cut_in_the_archive_and_whole_again_after(self):
        long = "x" * 1020
        ch = self.archived(self.proj, long)
        self.assertEqual(len(ch.topic), 1024)
        run(self.arch.on_interaction(Click(ch, "restore")))
        self.assertEqual(ch.topic, long)

    def test_a_topic_changed_in_the_archive_keeps_the_change_without_the_note(self):
        ch = self.archived(self.proj, "old")
        ch.topic = ch.topic.replace("old", "new words")
        run(self.arch.on_interaction(Click(ch, "restore")))
        self.assertEqual(ch.topic, "new words")

    def test_a_channel_moved_into_the_archive_by_hand_says_where_from_its_note(self):
        """The owner's batch (dcctl move, the note written by hand, no record): /archive there keeps the note's word."""
        shelf = self.g.cat(config.ARCHIVE_CATEGORY)
        ch = Ch(self.g, "idle", shelf, "📦 原分類：proj · 封存於 2026-10-20\n舊主題")
        self.bot.channels_state[str(ch.id)] = {"cwd": "/w", "tmux": "dc-idle", "session_id": "sid"}
        self.put(ch)
        rec = self.state(ch)["archived"]
        self.assertEqual((rec["from"], rec["topic"]), ({"id": self.proj.id, "name": "proj"}, "舊主題"))
        self.assertEqual(time.strftime("%Y-%m-%d", time.localtime(rec["at"])), "2026-10-20")
        self.assertEqual(ch.topic.count("📦"), 1, "one note, not two")
        self.assertIs(ch.category, shelf, "left in the archive category it is in")
        self.assertEqual(ch.edits, [], "its note says the same: nothing to change")

    def test_a_thread_is_refused(self):
        class Thread(discord.Thread):
            def __init__(self):
                self.name, self.id = "t", 5
        self.assertIn("討論串不能封存", run(self.arch.put(Thread()))[0])


class Pure(unittest.TestCase):
    def test_topic_note(self):
        rec = {"from": {"id": 1, "name": "proj"}, "at": time.mktime((2026, 10, 20, 12, 0, 0, 0, 0, -1))}
        t = archive.topic_with("舊", rec)
        self.assertEqual(t, "📦 原分類：proj · 封存於 2026-10-20\n舊")
        self.assertEqual(archive.topic_without(t), "舊")
        self.assertEqual(archive.noted(t)[0], "proj")
        self.assertEqual(archive.topic_with("", dict(rec, **{"from": None})), "📦 原分類：（沒有分類） · 封存於 2026-10-20")
        self.assertEqual(archive.topic_with("", {"at": rec["at"]}), "📦 原分類：（不明） · 封存於 2026-10-20", "a record that does not say")
        self.assertEqual(archive.noted("說到 📦 原分類：proj · 封存於 2026-10-20 這句"), None, "only at the start")
        self.assertEqual(archive.topic_without("無關的主題"), "無關的主題")


class Detach(unittest.TestCase):
    def test_detach_remembers_where_it_was(self):
        from bot import main as botmain
        g = Guild()
        proj = g.cat("proj")
        ch = Ch(g, "mine", proj, "t")
        bot = botmain.Bot.__new__(botmain.Bot)
        bot.channels_state = {str(ch.id): {"cwd": "/w", "pane": P, "pane_session": "s", "session_id": "sid",
                                           "shown": ["sid"], "history": ["old", "sid"]}}
        bot.save = lambda: None
        closed = []

        async def close(c, kill=True):
            closed.append(kill)
        bot.tmux = type("T", (), {"close": staticmethod(close)})()

        async def unbind_any(cid, host=None):
            pass
        with mock.patch.object(routes, "unbind_any", unbind_any), mock.patch.object(tmuxio, "label", lambda p: "lap"):
            text = run(botmain.Bot.detach(bot, ch, bot.channels_state[str(ch.id)]))
        rec = bot.channels_state[str(ch.id)]["archived"]
        self.assertEqual((rec["from"], rec["why"], closed), ({"id": proj.id, "name": "proj"}, kinds.DETACHED, [False]))
        self.assertEqual(ch.category.name, config.ARCHIVE_CATEGORY)
        self.assertTrue(ch.topic.startswith("📦 原分類：proj"))
        self.assertIn("移到「%s」" % config.ARCHIVE_CATEGORY, text)
        st = bot.channels_state[str(ch.id)]
        self.assertEqual((st["shown"], st["history"]), (["sid"], ["old", "sid"]), "what it has shown and held stays")
        self.assertIn("shown", archive.STAY)
        self.assertIn("history", archive.STAY)


class Adopt(Base):
    """At a start: the owner's batch moved in by hand (a note in the topic, no record), and the old "archive"."""

    def setUp(self):
        super().setUp()
        import tempfile
        state = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(state, ignore_errors=True))
        p = mock.patch.object(config, "STATE", state)  # (where the list of those said to be left alone is kept)
        p.start()
        self.addCleanup(p.stop)
        self.shelf = self.g.cat(config.ARCHIVE_CATEGORY)
        self.legacy = self.g.cat(archive.LEGACY)
        self.old = time.time() - (config.IDLE_HOURS + 1) * 3600

    def adopt(self):
        run(self.arch.adopt_all(self.g))

    def test_the_owners_batch_gets_records_from_the_notes(self):
        a = Ch(self.g, "idle-chat", self.legacy, "📦 原分類：proj · 封存於 2026-10-18\n舊的")
        self.bot.channels_state[str(a.id)] = {"cwd": "/w", "tmux": "dc-idle", "session_id": "sid", "last_active": self.old}
        b = Ch(self.g, "notes", self.shelf, "📦 原分類：（沒有分類） · 封存於 2026-10-18")
        self.bot.channels_state[str(b.id)] = {"cwd": "/w", "plain": True}
        c = Ch(self.g, "bare", self.legacy, "沒有註記")
        self.bot.channels_state[str(c.id)] = {"cwd": "/w/repo", "plain": True}
        self.adopt()
        ra = self.state(a)["archived"]
        self.assertEqual((ra["from"], ra["why"], ra["session_id"], ra["tmux"]),
                         ({"id": self.proj.id, "name": "proj"}, kinds.ADOPTED, "sid", "dc-idle"))
        self.assertEqual(self.bot.tmux.closed, [(a.id, True)], "the bot's own idle session: as the reaper")
        self.assertIs(a.category, self.shelf, "out of the old \"archive\"")
        self.assertIsNone(self.state(b)["archived"]["from"])
        self.assertIs(b.category, self.shelf)
        self.assertEqual(b.edits, [], "its note says it already")
        self.assertNotIn("from", self.state(c)["archived"], "no note: taken back by its folder")
        self.assertTrue(c.topic.startswith("📦 原分類：（不明）"))
        line = self.bot.audits[-1]
        self.assertIn("補記 3 個（其中 2 個是從「archive」搬進來的）", line, "those moved in from \"archive\" counted too")
        self.assertNotIn("已經有封存紀錄", line)
        self.assertIn("「archive」分類已經空了，沒有刪", line)
        self.assertIn(self.legacy, self.g.categories, "never deleted")
        with mock.patch.object(routes, "project_name", lambda cwd: "repo"):
            run(self.arch.on_interaction(Click(c, "restore")))
        self.assertEqual(c.category.name, "repo")
        self.assertEqual(c.topic, "沒有註記")

    def test_one_in_use_is_left_and_listed(self):
        a = Ch(self.g, "busy", self.legacy)
        self.bot.channels_state[str(a.id)] = {"cwd": "/w", "tmux": "dc-b", "session_id": "s", "last_active": time.time()}
        b = Ch(self.g, "working", self.legacy)
        self.bot.channels_state[str(b.id)] = {"cwd": "/w", "tmux": "dc-w", "session_id": "s", "last_active": self.old, "bg": True}
        self.adopt()
        for ch in (a, b):
            self.assertNotIn("archived", self.state(ch))
            self.assertIs(ch.category, self.legacy)
        self.assertEqual(self.bot.tmux.closed, [])
        self.assertIn("這次沒動：#busy、#working", self.bot.audits[-1])
        self.assertNotIn("已經空了", self.bot.audits[-1])
        n = len(self.bot.audits)
        self.adopt()
        self.assertEqual(len(self.bot.audits), n, "the same two again at the next start: nothing said (manager 2026-10-20)")
        c = Ch(self.g, "new-one", self.legacy)
        self.bot.channels_state[str(c.id)] = {"cwd": "/w", "tmux": "dc-n", "session_id": "s", "last_active": time.time()}
        self.adopt()
        self.assertIn("這次沒動：#busy、#working、#new-one", self.bot.audits[-1], "one not said before: the list again")
        n = len(self.bot.audits)
        self.adopt()
        self.assertEqual(len(self.bot.audits), n)
        self.bot.channels_state[str(a.id)]["last_active"] = self.old  # idle now: adopted, and said
        self.adopt()
        self.assertIn("補記 1 個（其中 1 個是從「archive」搬進來的）", self.bot.audits[-1])
        self.assertIn("這次沒動：#working、#new-one", self.bot.audits[-1])

    def test_windows_in_the_owners_tmux_are_let_go_of_not_closed(self):
        home = {"socket": "/u", "session": "main", "window": ""}
        a = Ch(self.g, "home", self.shelf)
        self.bot.channels_state[str(a.id)] = {"cwd": "/w", "home": home, "pane": P, "session_id": "s", "last_active": self.old}
        t = Ch(self.g, "sh", self.shelf)
        self.bot.channels_state[str(t.id)] = {"cwd": "/w", "kind": "terminal", "home": home, "pane": dict(P, id="%9")}
        self.adopt()
        self.assertEqual(self.bot.tmux.closed, [(a.id, False)])
        self.assertEqual(self.killed, [], "a shell may be running a command the restarted bot does not know of")
        self.assertEqual(self.state(a)["archived"]["pane"], P, "taken back to the same window if it is still there")
        self.assertEqual(self.state(t)["pane"]["id"], "%9")
        self.assertIn("視窗還開著、只是不再跟著的：#home", self.bot.audits[-1])

    def test_archived_ones_stay_and_nothing_to_say(self):
        a = Ch(self.g, "done", self.shelf, "t")
        self.bot.channels_state[str(a.id)] = {"archived": {"why": "detached"}, "cwd": "/w"}
        self.adopt()
        self.assertEqual((a.edits, self.bot.audits), ([], []))
        b = Ch(self.g, "moved", self.legacy)
        self.bot.channels_state[str(b.id)] = {"archived": {"why": "detached"}, "cwd": "/w"}
        self.adopt()
        self.assertIs(b.category, self.shelf)
        self.assertEqual(self.state(b)["archived"], {"why": "detached"}, "the record as it was")
        self.assertIn("補記 0 個；已經有封存紀錄、從「archive」搬進「%s」的 1 個" % config.ARCHIVE_CATEGORY, self.bot.audits[-1])

    def test_fifty_to_a_category_when_moving_the_old_ones_in(self):
        for i in range(archive.SHELF_MAX - 1):
            Ch(self.g, "x%d" % i, self.shelf)
            self.bot.channels_state[str(self.g.text_channels[-1].id)] = {"archived": {}, "cwd": "/w"}
        for i in range(3):
            Ch(self.g, "old%d" % i, self.legacy)
            self.bot.channels_state[str(self.g.text_channels[-1].id)] = {"cwd": "/w", "plain": True}
        self.adopt()
        self.assertEqual(len(self.shelf.channels), archive.SHELF_MAX)
        second = next(c for c in self.g.categories if c.name == "%s 2" % config.ARCHIVE_CATEGORY)
        self.assertEqual(len(second.channels), 2)


class Spool(unittest.TestCase):
    def test_dcctl_archive_is_done_by_the_bot_and_said_in_audit(self):
        from bot import posts
        g = Guild()
        ch = Ch(g, "logs", g.cat("proj"))
        asked = []

        class Arch(object):
            async def put(self, c, force=False):
                asked.append((c.id, force))
                return "📦 已封存", None
        bot = Bot()
        bot.archive, bot.get_channel = Arch(), lambda cid: ch if cid == ch.id else None
        bot.get_guild = lambda gid: g
        p = posts.Posts(bot)
        run(p.handle({"channel": str(ch.id), "archive": True, "from_name": "dcctl archive"}))
        self.assertEqual(asked, [(ch.id, False)], "one at work is not stopped from here")
        self.assertEqual(bot.audits, ["dcctl archive #logs：📦 已封存"])
        run(p.handle({"channel": "99999", "archive": True}))
        self.assertIn("找不到頻道 99999", bot.audits[-1])

    def test_dcctl_archive_queues_a_post(self):
        import importlib.machinery
        import importlib.util
        import os
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        loader = importlib.machinery.SourceFileLoader("dcctl_arch", os.path.join(root, "tools", "dcctl"))
        d = importlib.util.module_from_spec(importlib.util.spec_from_loader("dcctl_arch", loader))
        loader.exec_module(d)
        from bot import postbox
        written = []
        chans = [{"id": "77", "name": "logs", "type": 0, "position": 0}]
        with mock.patch.object(d, "channels", lambda: chans), mock.patch.object(postbox, "write", written.append), \
                mock.patch.object(postbox, "heartbeat_age", lambda: 5), mock.patch.object(d, "call", mock.Mock(side_effect=AssertionError("no REST"))), \
                mock.patch("sys.stdout"):
            d.main(["archive", "logs"])
        self.assertEqual(written, [{"channel": "77", "archive": True, "from_name": "dcctl archive"}])


if __name__ == "__main__":
    unittest.main()


class Leave(Base):
    """Owner's report 2026-10-20: a channel that becomes something again while it sits in the archive (the setup card, /attach into it,
    dcpost --new-claude) is moved out of it: typing in the archive offers the archive's buttons, never Claude."""
    def shelved(self, rec_from):
        shelf = run(archive.shelf(self.g))
        ch = Ch(self.g, "project-a-0", category=shelf)
        rec = {"why": kinds.DETACHED, "at": time.time(), "topic": "lap · %5"}
        if rec_from is not None:
            rec["from"] = rec_from
        ch.topic = archive.topic_with("lap · %5", rec)
        return ch, rec

    def test_back_where_it_came_from_its_note_gone(self):
        cv = self.g.cat("project-a")
        ch, rec = self.shelved({"id": cv.id, "name": "project-a"})
        self.assertIs(run(self.arch.leave(ch, rec, "/w")), cv)
        self.assertEqual((ch.category, ch.topic), (cv, "lap · %5"))

    def test_its_category_gone_by_its_folder_else_no_category(self):
        ch, rec = self.shelved({"id": 12345, "name": "gone-category"})
        with mock.patch.object(routes, "project_name", lambda cwd: None):
            self.assertIsNone(run(self.arch.leave(ch, rec, "/w")))
        self.assertIsNone(ch.category, "never left in the archive")

    def test_a_move_discord_refuses_is_logged_and_the_caller_goes_on(self):
        import contextlib
        import io
        ch, rec = self.shelved(None)

        async def refused(**kw):
            raise discord.HTTPException(type("R", (), {"status": 403, "reason": "Forbidden"})(), "Missing Permissions")
        ch.edit = refused
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertIs(run(self.arch.leave(ch, rec, "/w")), False, "#code-review: never raised to attach or new_claude")
        self.assertEqual(ch.sent, ["⚠️ 頻道沒能搬出封存，請手動移到原分類。（頻道照常可以用）"], "said in the channel (manager)")

    def test_not_in_the_archive_nothing_moves(self):
        ch = Ch(self.g, "x", category=self.proj)
        self.assertIs(run(self.arch.leave(ch, None, "/w")), False)
        self.assertEqual(ch.edits, [])

    def test_set_up_from_the_card_it_comes_out_and_says_so(self):
        from bot import setup
        cv = self.g.cat("project-a")
        ch, rec = self.shelved({"id": cv.id, "name": "project-a"})
        st = self.bot.chan(ch)
        st.update({"archived": rec, "home": {"host": "lap", "session": "project-a"}})
        self.bot.archive = self.arch
        s = setup.Setup(self.bot)
        run(s.done(ch, st, "會開在 lap 的 tmux `project-a`"))
        self.assertIs(ch.category, cv)
        self.assertNotIn("archived", st)
        self.assertIn("頻道從封存搬回「project-a」", self.bot.audits[-1])
        self.assertEqual(self.bot.tmux.started, [ch.id])


class AttachInto(Base):
    """Owner's report 2026-10-20: /attach into a channel not set up yet that sits in the archive moves it out; the history an attach
    posts is remembered as shown (a !resume of it later posts only its last turns)."""
    def test_out_of_the_archive_and_its_history_remembered(self):
        import json
        from bot import main as botmain, remote as bot_remote
        cv = self.g.cat("project-a")
        shelf = run(archive.shelf(self.g))
        ch = Ch(self.g, "project-a-0", category=shelf)
        rec = {"why": kinds.DETACHED, "at": time.time(), "topic": "t", "from": {"id": cv.id, "name": "project-a"}}
        bot = botmain.Bot.__new__(botmain.Bot)
        bot.channels_state = {str(ch.id): {"archived": rec, "cwd": "/w"}}
        bot.save = lambda: None
        bot.archive = archive.Archive(bot)
        backfills = []

        async def backfill(c, messages):
            backfills.append(messages)
        bot.backfill = backfill

        async def bind_any(pane, cid):
            pass

        async def dcremote(host, *args, **kw):
            return 0, json.dumps({"session_id": "sid-x", "turns": [["asked", "q", "a", ""]]})
        pane = {"host": "lap", "socket": "/s", "id": "%1", "pid": "9", "session": "project-a", "window": "w", "cwd": "/w"}

        async def go():
            bot.loop = asyncio.get_running_loop()
            await botmain.Bot.attach_one(bot, self.g, pane, "project-a", into=ch)
            await asyncio.sleep(0)
        with mock.patch.object(routes, "bind_any", bind_any), mock.patch.object(bot_remote, "dcremote", dcremote):
            run(go())
        st = bot.channels_state[str(ch.id)]
        self.assertIs(ch.category, cv, "out of the archive")
        self.assertNotIn("archived", st)
        self.assertEqual(st["shown"], ["sid-x"], "the history posted by the attach is remembered")
        self.assertEqual(len(backfills), 1)


class TypingDecidedByTheRecord(Base):
    """Owner's report 2026-10-20: what is archived is what has a record, not what sits in an archive category: a
    channel set up again whose move out Discord refused is used where it is; one with a record is offered the ways
    back wherever it is."""
    def typed(self, ch, st):
        from bot import main as botmain
        bot = botmain.Bot.__new__(botmain.Bot)
        bot.channels_state = {str(ch.id): st}
        offered = []

        async def offer(c):
            offered.append(c.id)
        bot.archive = type("A", (), {"offer": staticmethod(offer)})()
        bot.catchup = type("C", (), {"mark": staticmethod(lambda m: True)})()
        bot.posts = type("P", (), {"owner_spoke": staticmethod(lambda cid: None)})()
        self.typed_files = []

        async def typed(c, text):
            self.typed_files.append(text)
        bot.files = type("F", (), {"typed": staticmethod(typed)})()
        owner = type("U", (), {"id": 111, "bot": False})()
        msg = type("M", (), {"author": owner, "channel": ch, "content": "hello", "mentions": []})()
        run(botmain.Bot.on_message(bot, msg))
        return offered

    def test_in_the_archive_category_without_a_record_is_not_offered(self):
        shelf = run(archive.shelf(self.g))
        ch = Ch(self.g, "x", category=shelf)
        self.assertEqual(self.typed(ch, {"kind": "files"}), [], "used where it is")
        self.assertEqual(self.typed_files, ["hello"], "what it is (a files channel here) gets what was typed")

    def test_a_record_is_offered_wherever_the_channel_is(self):
        ch = Ch(self.g, "y", category=self.proj)
        self.assertEqual(self.typed(ch, {"archived": {"why": kinds.DETACHED}}), [ch.id])
