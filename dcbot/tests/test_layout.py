"""C-57 D1: a new installation's basic layout (the system category at the bottom, #audit and #guide in it, #update in
the main area), the system category kept last when the bot makes another one (if it was last), its channels not taken for a project's,
and the spool's move_to (altention dc post --move-to) that moves a channel into a category."""
import asyncio
import os
import unittest
from unittest import mock

from bot import config, layout, posts
from tests import tmuxguard  # noqa: F401,E402  (no test reaches a real tmux server)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class Cat(object):
    def __init__(self, guild, name, position):
        self.guild, self.name, self.position, self.edits = guild, name, position, []
        self.id = 500 + position

    @property
    def text_channels(self):
        return [c for c in self.guild.text_channels if c.category is self]

    async def edit(self, position=None):
        """As discord.py 2.7's abc.GuildChannel._move: out of the sorted list, then in before the first whose position
        is at least the one asked for (else last), and every position numbered again."""
        self.edits.append(position)
        others = sorted((c for c in self.guild.categories if c is not self), key=lambda c: c.position)
        index = next((i for i, c in enumerate(others) if c.position >= position), len(others))
        others.insert(index, self)
        for i, c in enumerate(others):
            c.position = i


class Ch(object):
    def __init__(self, guild, name, category=None, topic=None):
        self.guild, self.name, self.category, self.topic, self.moves = guild, name, category, topic, []
        self.id = 900 + len(guild.text_channels)
        self.sent = []

    async def edit(self, category=None):
        self.moves.append(getattr(category, "name", None))
        self.category = category

    async def send(self, text=None, **kw):
        self.sent.append(text)


class Guild(object):
    def __init__(self):
        self.categories, self.text_channels, self.made = [], [], []

    async def create_category(self, name):
        cat = Cat(self, name, len(self.categories))  # Discord puts a new category last
        self.categories.append(cat)
        self.made.append(("category", name))
        return cat

    async def create_text_channel(self, name, topic=None, category=None):
        ch = Ch(self, name, category, topic)
        self.text_channels.append(ch)
        self.made.append(("channel", name, getattr(category, "name", None)))
        return ch

    def order(self):
        return [c.name for c in sorted(self.categories, key=lambda c: c.position)]


def run(coro):
    return asyncio.run(coro)


class FirstLayout(unittest.TestCase):
    def test_an_empty_server_gets_the_system_category_last_and_update_in_the_main_area(self):
        g = Guild()
        audit = run(layout.first_layout(g))
        self.assertEqual(g.made, [("category", config.SYSTEM_CATEGORY), ("channel", config.AUDIT_CHANNEL, config.SYSTEM_CATEGORY),
                                  ("channel", config.GUIDE_CHANNEL, config.SYSTEM_CATEGORY), ("channel", config.UPDATE_CHANNEL, None)])
        self.assertEqual(audit.name, config.AUDIT_CHANNEL)
        update = next(c for c in g.text_channels if c.name == config.UPDATE_CHANNEL)
        self.assertIsNone(update.category)
        self.assertNotIn("Claude", update.topic.replace("不聊天", ""), "a channel to read, no conversation")

    def test_a_guide_made_before_the_first_start_is_moved_in_one_the_owner_placed_is_left(self):
        """dcctl guide may run before the bot first starts (it makes #guide outside any category)."""
        g = Guild()
        mine = run(g.create_category("我的"))
        guide = run(g.create_text_channel(config.GUIDE_CHANNEL))
        audit = run(g.create_text_channel(config.AUDIT_CHANNEL, category=mine))
        upd = run(g.create_text_channel(config.UPDATE_CHANNEL, category=mine))
        g.made.clear()
        run(layout.first_layout(g))
        self.assertEqual(guide.category.name, config.SYSTEM_CATEGORY)
        self.assertEqual((audit.category, upd.category), (mine, mine), "the owner's placing stands")
        self.assertEqual(g.made, [("category", config.SYSTEM_CATEGORY)], "no second channel of any name")
        self.assertEqual(g.order()[-1], config.SYSTEM_CATEGORY)

    def test_discord_refusing_a_channel_does_not_stop_the_others(self):
        import discord
        g = Guild()
        real = g.create_text_channel

        async def create(name, topic=None, category=None):
            if name == config.GUIDE_CHANNEL:
                raise discord.HTTPException(mock.Mock(status=403, reason="Forbidden"), "Missing Permissions")
            return await real(name, topic=topic, category=category)
        g.create_text_channel = create
        self.assertIsNotNone(run(layout.first_layout(g)))
        self.assertIn(config.UPDATE_CHANNEL, [c.name for c in g.text_channels])


class SystemLast(unittest.TestCase):
    def test_a_category_the_bot_makes_later_goes_above_the_system_one(self):
        g = Guild()
        run(layout.first_layout(g))
        for name in ("proj-a", config.ARCHIVE_CATEGORY, "proj-b"):
            run(layout.category(g, name))
            self.assertEqual(g.order()[-1], config.SYSTEM_CATEGORY, name)
        self.assertEqual(g.order(), ["proj-a", config.ARCHIVE_CATEGORY, "proj-b", config.SYSTEM_CATEGORY])
        n = len([c for c in g.categories])
        run(layout.category(g, "proj-a"))
        self.assertEqual(len(g.categories), n, "an existing category is used, not made again")

    def test_a_system_category_not_last_stays_where_the_owner_put_it(self):
        """Manager 2026-10-20 (it went last before, #code-review of 1af1489): the owner put the system category above,
        between dividers; a category the bot makes later leaves it there."""
        g = Guild()
        for name in ("━━ 日常 ━━", "機器人", "━━ 系統 ━━", config.SYSTEM_CATEGORY, "━━ 專案 ━━", "proj"):
            run(g.create_category(name))
        run(layout.category(g, "proj-2"))
        self.assertEqual(g.order(), ["━━ 日常 ━━", "機器人", "━━ 系統 ━━", config.SYSTEM_CATEGORY, "━━ 專案 ━━", "proj", "proj-2"])
        system = next(c for c in g.categories if c.name == config.SYSTEM_CATEGORY)
        self.assertEqual(system.edits, [], "not moved at all")
        self.assertFalse(layout.system_is_last(g))

    def test_system_last_with_a_tie_is_not_last(self):
        g = Guild()
        for name in ("proj", config.SYSTEM_CATEGORY):
            run(g.create_category(name))
        self.assertTrue(layout.system_is_last(g))
        g.categories[0].position = g.categories[1].position
        self.assertFalse(layout.system_is_last(g))

    def test_the_move_is_what_discord_py_sends(self):
        """The real discord.py move (abc.GuildChannel._move) on stand-in channels, its payload collected: the system
        category asked to the bottom ends there."""
        import discord.abc
        g = Guild()
        for name in ("機器人", config.SYSTEM_CATEGORY, "proj"):
            run(g.create_category(name))
        sent = []

        class Http(object):
            async def bulk_channel_update(self, guild_id, payload, reason=None):
                sent.append(payload)

        class Chan(object):
            def __init__(self, cat):
                self.id, self.position, self._sorting_bucket = cat.id, cat.position, 4
                self.guild, self._state = guild, type("S", (), {"http": Http()})()
        guild = type("G", (), {"id": 1})()
        guild.channels = [Chan(c) for c in g.categories]
        system = next(c for c in guild.channels if c.id == next(x.id for x in g.categories if x.name == config.SYSTEM_CATEGORY))

        async def edit(position=None):
            await discord.abc.GuildChannel._move(system, position, reason=None)
        real = next(c for c in g.categories if c.name == config.SYSTEM_CATEGORY)
        real.edit = edit
        run(layout.keep_system_last(g))
        order = [p["id"] for p in sorted(sent[-1], key=lambda p: p["position"])]
        self.assertEqual(order[-1], system.id, sent[-1])

    def test_no_system_category_nothing_moves(self):
        g = Guild()
        run(layout.category(g, "proj-a"))
        run(layout.keep_system_last(g))
        self.assertEqual(g.order(), ["proj-a"])

    def test_every_place_the_bot_makes_a_category_goes_through_layout(self):
        import glob
        import re
        for path in glob.glob(os.path.join(ROOT, "bot", "*.py")):
            if path.endswith("layout.py"):
                continue
            with open(path, encoding="utf-8") as f:
                self.assertIsNone(re.search(r"\.create_category\(", f.read()), path)


class NotAProject(unittest.TestCase):
    def test_the_owners_dividers_are_no_project(self):
        for name in ("━━ 日常 ━━", "━━ 專案 ━━", config.ARCHIVE_CATEGORY, config.SYSTEM_CATEGORY, "機器人"):
            self.assertTrue(config.not_a_project(name), name)
        for name in ("proj", "web-app", "插件：learn", "a ━━ b"):
            self.assertFalse(config.not_a_project(name), name)

    def test_a_channel_put_under_a_divider_works_in_the_engine(self):
        from bot import main as botmain
        g = Guild()
        div = run(g.create_category("━━ 專案 ━━"))
        old = Ch(g, "old", div)
        g.text_channels.append(old)
        bot = botmain.Bot.__new__(botmain.Bot)
        bot.channels_state = {str(old.id): {"cwd": "/somewhere/a-project"}}
        new = Ch(g, "new-one", div)
        g.text_channels.append(new)
        st = botmain.Bot.chan(bot, new)
        self.assertNotIn("cwd_from", st)
        self.assertEqual(st["cwd"], os.path.dirname(config.ROOT))

    def test_a_channel_in_the_system_category_works_in_the_engine_not_a_sibling_folder(self):
        from bot import main as botmain
        self.assertIn(config.SYSTEM_CATEGORY, config.NOT_PROJECTS)
        g = Guild()
        run(layout.first_layout(g))
        audit = next(c for c in g.text_channels if c.name == config.AUDIT_CHANNEL)
        bot = botmain.Bot.__new__(botmain.Bot)
        bot.channels_state = {str(audit.id + 1): {"cwd": "/somewhere/a-project"}}
        new = Ch(g, "new-one", audit.category)
        g.text_channels.append(new)
        st = botmain.Bot.chan(bot, new)
        self.assertNotIn("cwd_from", st)
        self.assertEqual(st["cwd"], os.path.dirname(config.ROOT))


class OwnChannels(unittest.TestCase):
    def bot(self, new, state):
        from bot import main as botmain
        bot = botmain.Bot.__new__(botmain.Bot)
        bot.new_install, bot.channels_state, bot.audit_ch, bot.calls = new, state, None, []

        async def first_chat(guild):
            bot.calls.append("first_chat")

        async def ensure(guild, name, topic):
            bot.calls.append(("ensure", name))
            return "audit-channel"
        bot.first_chat, bot.ensure_channel = first_chat, ensure
        return bot

    def test_only_a_new_installation_gets_the_layout(self):
        from bot import main as botmain
        g = Guild()
        bot = self.bot(True, {})
        with mock.patch.object(botmain, "setup_done", lambda: True):  # (the setup channel's own tests: test_setup_channel)
            run(bot.own_channels(g))
        self.assertEqual(bot.calls, ["first_chat"])
        self.assertEqual(bot.audit_ch.category.name, config.SYSTEM_CATEGORY)
        for new, state in ((False, {}), (True, {"1": {}})):
            g = Guild()
            bot = self.bot(new, state)
            with mock.patch.object(botmain, "setup_done", lambda: True):
                run(bot.own_channels(g))
            self.assertEqual((bot.calls, g.made, bot.audit_ch), ([("ensure", config.AUDIT_CHANNEL)], [], "audit-channel"),
                             "an installation that is not new is left as it is")


class UpdateChannel(unittest.TestCase):
    def test_the_instance_says_else_update(self):
        for said, want in (("news", "news"), (" #upd ", "upd"), (None, "update"), ("", "update"), (5, "update"), ("#", "update")):
            with mock.patch("altention.config.setting", lambda path, default=None, s=said: s if path == "core.update_channel" else None):
                self.assertEqual(config.update_channel(), want, said)

        def broken(path, default=None):
            raise ValueError("bad json")
        with mock.patch("altention.config.setting", broken):
            self.assertEqual(config.update_channel(), "update")


class MoveTo(unittest.TestCase):
    def setUp(self):
        self.g = Guild()
        run(layout.first_layout(self.g))
        self.audit = next(c for c in self.g.text_channels if c.name == config.AUDIT_CHANNEL)
        self.audit.category = None  # an installation from before the system category
        self.guide = next(c for c in self.g.text_channels if c.name == config.GUIDE_CHANNEL)
        self.guide.category = None
        self.g.categories.remove(next(c for c in self.g.categories if c.name == config.SYSTEM_CATEGORY))
        run(self.g.create_category("proj"))
        g, audits = self.g, []

        class Bot(object):
            channels_state = {}

            def get_guild(self, gid):
                return g

            def get_channel(self, cid):
                return next((c for c in g.text_channels if c.id == cid), None)

            def chan(self, ch):
                return {}

            def save(self):
                pass

            async def audit(self, text):
                audits.append(text)
        self.audits = audits
        self.p = posts.Posts(Bot())

    def post(self, **kw):
        run(self.p.handle(dict({"from_name": "lap 上的 Claude（setup）"}, **kw)))

    def test_the_system_category_is_made_at_the_bottom_and_the_channel_moved_in(self):
        self.post(channel=config.AUDIT_CHANNEL, move_to=config.SYSTEM_CATEGORY, text="")
        self.assertEqual(self.audit.category.name, config.SYSTEM_CATEGORY)
        self.assertEqual(self.g.order()[-1], config.SYSTEM_CATEGORY)
        self.post(channel=config.GUIDE_CHANNEL, move_to=config.SYSTEM_CATEGORY, text="補齊：系統頻道放一起")
        self.assertEqual(self.guide.category, self.audit.category, "the same category, not a second one")
        self.assertEqual([c.name for c in self.g.categories].count(config.SYSTEM_CATEGORY), 1)
        self.assertIn("補齊：系統頻道放一起", self.audits[-1])
        self.assertIn("#%s 移到分類「%s」" % (config.GUIDE_CHANNEL, config.SYSTEM_CATEGORY), self.audits[-1])

    def test_already_there_nothing_moves_unknown_channel_nothing_made(self):
        self.post(channel=config.AUDIT_CHANNEL, move_to="proj", text="")
        self.audit.moves.clear()
        self.post(channel=config.AUDIT_CHANNEL, move_to="proj", text="")
        self.assertEqual(self.audit.moves, [])
        n = len(self.g.text_channels)
        self.post(channel="no-such", move_to="proj", text="")
        self.assertEqual(len(self.g.text_channels), n, "a channel is never made by --move-to")
        self.assertIn("找不到頻道 no-such", self.audits[-1])
        self.assertNotIn(("channel", "no-such", None), self.g.made)


class DcpostFlag(unittest.TestCase):
    def build(self, argv):
        import importlib.util
        spec = importlib.util.spec_from_file_location("dcpost_t", os.path.join(os.path.dirname(ROOT), "altention", "dc", "post.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod.build(argv, "", {}, "lap", "/x/setup")

    def test_move_to_needs_no_text_and_a_name(self):
        post = self.build(["audit", "--move-to", "系統"])
        self.assertEqual((post["channel"], post["move_to"], post["text"]), ("audit", "系統", ""))
        self.assertEqual(self.build(["#guide", "--move-to", " 系統 ", "why"])["text"], "why")
        with self.assertRaises(SystemExit):
            self.build(["audit", "--move-to"])
        with self.assertRaises(SystemExit):
            self.build(["audit", "--move-to", " "])
