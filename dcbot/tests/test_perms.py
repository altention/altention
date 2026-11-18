"""C-191 (owner 11-12, manager 11-17): the bot looks at its own permissions in the background (bot/perms.py): said
once in #audit, a change said again; no Administrator: one #update card, ever, with why and the link; given later:
the card says so; nothing it cannot look at stops anything."""
import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))))
from altention import testing as _testing  # noqa: E402
_testing.guard_home()  # tests never touch the real home (altention/testing.py)
import asyncio
import shutil
import tempfile
import types
import unittest
from unittest import mock

from altention.dc import invite
from bot import config, perms, standby
from tests import tmuxguard  # noqa: F401  (no test reaches a real tmux server)

GUILD = 77
NO_ADMIN, ADMIN = invite.PERMISSION_BITS, invite.WITH_ADMIN


class Bot(object):
    def __init__(self, bits=NO_ADMIN, app=5):
        self.bits, self.application_id, self.audits, self.on_server = bits, app, [], True
        self.loop = None

    def get_guild(self, gid):
        if gid != GUILD or not self.on_server:
            return None
        me = types.SimpleNamespace(guild_permissions=types.SimpleNamespace(value=self.bits))
        return types.SimpleNamespace(id=GUILD, me=me)

    async def audit(self, text):
        self.audits.append(text)


class Looks(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.queued = []
        for p in (mock.patch.object(config, "STATE", self.tmp), mock.patch.object(config, "GUILD_ID", GUILD),
                  mock.patch.object(perms.postbox, "write", self.queued.append)):
            p.start()
            self.addCleanup(p.stop)
        self.bot = Bot()
        self.p = perms.Perms(self.bot)

    def look(self):
        asyncio.run(self.p.check())
        self.bot.cards_shown = [post["card"] for post in self.queued]

    def test_no_administrator_one_card_ever_with_why_and_the_link(self):
        self.look()
        self.assertEqual(self.bot.audits, ["[權限] 機器人目前的權限：沒有管理員（值 %d）" % NO_ADMIN])
        (card,) = self.bot.cards_shown
        self.assertEqual((card["id"], card["buttons"]), ("bot-admin", []))
        for words in ("為什麼要", "社群伺服器", "不給也照常能用", "不是重裝", "permissions=%d" % ADMIN, "guild_id=%d" % GUILD,
                      "permissions=%d" % NO_ADMIN):
            self.assertIn(words, card["text"])
        st = perms.load()
        self.assertEqual((st["bits"], st["admin"], st["missing"], st["guild"]), (NO_ADMIN, False, [], GUILD))
        self.assertEqual(st["grant_url"], invite.url(5, GUILD))
        self.assertTrue(st["carded"])
        self.look()
        self.assertEqual((len(self.bot.audits), len(self.bot.cards_shown)), (1, 1), "once: the same again says nothing")

    def test_given_later_said_and_the_card_says_so(self):
        self.look()
        self.bot.bits = ADMIN
        self.look()
        self.assertEqual(self.bot.audits[1], "[權限] 機器人的權限變了：沒有管理員 → 有管理員（值 %d → %d；Discord 的審核日誌不記這件事）"
                         % (NO_ADMIN, ADMIN))
        closed = self.bot.cards_shown[1]
        self.assertTrue(closed["close"])
        self.assertIn("已經有「管理員」權限", closed["text"])
        self.assertIn("permissions=%d" % NO_ADMIN, closed["text"], "the way back")
        self.look()
        self.assertEqual(len(self.bot.cards_shown), 2, "given and said so: the card not edited again at every start")
        self.bot.bits = NO_ADMIN
        self.look()
        self.assertIn("有管理員 → 沒有管理員", self.bot.audits[2])
        asked = self.bot.cards_shown[2]
        self.assertEqual((asked["id"], asked.get("close")), ("bot-admin", None), "the same card asks again (#code-review on 5c7b6a1e)")
        self.assertIn("要不要給機器人", asked["text"])
        self.assertNotIn("granted", perms.load())
        self.look()
        self.assertEqual(len(self.bot.cards_shown), 3, "once")
        self.bot.bits = ADMIN
        self.look()
        self.assertTrue(self.bot.cards_shown[3]["close"], "given again: said so again")

    def test_already_administrator_no_card_one_line(self):
        self.bot.bits = ADMIN
        self.look()
        self.look()
        self.assertEqual((self.bot.audits, self.bot.cards_shown), (["[權限] 機器人目前的權限：有管理員（值 %d）" % ADMIN], []))

    def test_one_of_the_14_missing_is_said(self):
        self.bot.bits = NO_ADMIN & ~(1 << 51)
        self.look()
        self.assertIn("沒有管理員，少了 PIN_MESSAGES", self.bot.audits[0])

    def test_nothing_to_look_at_nothing_done(self):
        self.bot.on_server = False
        self.look()
        self.assertEqual((self.bot.audits, self.bot.cards_shown, perms.load()), ([], [], {}))

    def test_no_application_id_yet_no_card(self):
        self.bot.application_id = None
        self.look()
        self.assertEqual((len(self.bot.audits), self.bot.cards_shown), (1, []))
        self.assertFalse(perms.load().get("carded"), "a card next time, once the link can be made")

    def test_a_look_that_breaks_is_skipped(self):
        async def broken(text):
            raise RuntimeError("Discord gone")
        self.bot.audit = broken
        self.look()  # (no exception out of check(): nothing waits on it)
        self.assertEqual(perms.load()["bits"], NO_ADMIN)

    def test_the_card_goes_through_the_post_queue(self):
        """Left as a file for the pump (shown and retried as altention.dc.card's): never a request from here; a card
        that could not be left is left the next time."""
        with mock.patch.object(perms.postbox, "write", side_effect=OSError("disk full")):
            self.look()
        self.assertFalse(perms.load().get("carded"))
        self.look()
        self.assertEqual(len(self.queued), 1)
        self.assertEqual(set(self.queued[0]), {"card", "from_name"}, "the update channel by default")
        shown = []

        async def show(post):
            shown.append(post)
        from bot import posts
        reader = posts.Posts(types.SimpleNamespace(get_channel=lambda cid: None, cards=types.SimpleNamespace(show=show)))
        asyncio.run(reader.handle(self.queued[0]))
        self.assertEqual(shown, self.queued, "the pump hands it to the cards as altention.dc.card's")

    def test_has_admin_and_what_a_feature_says(self):
        self.assertFalse(perms.has_admin(self.bot))
        self.bot.bits = ADMIN
        self.assertTrue(perms.has_admin(self.bot))
        self.bot.on_server = False
        self.assertFalse(perms.has_admin(self.bot), "cannot tell: not there")
        self.assertIn("#update", perms.need_admin())

    def test_kept_for_a_takeover(self):
        self.assertIn(perms.FILE, standby.SYNCED, "the standby says nothing again after taking over")


class Startup(unittest.TestCase):
    """The look every start takes (main.Bot.on_ready, the first time: with the pump; #code-review-2 on 5c7b6a1e)."""
    def test_on_ready_looks_once(self):
        from bot import main
        for pumping, times in ((False, 1), (True, 0)):
            bot = mock.Mock()
            bot.pumping = pumping
            bot.get_guild.return_value = None
            bot.audit = mock.AsyncMock()
            bot.sync_menu = mock.AsyncMock()
            bot.loop.create_task.side_effect = lambda coro: (coro.close() if asyncio.iscoroutine(coro) else None, mock.Mock())[1]
            with mock.patch.object(main, "git", return_value="abc"), \
                    mock.patch.object(main, "NOTICE", "/nonexistent/notice"), \
                    mock.patch.object(main, "RESTART_FLAG", "/nonexistent/flag"), \
                    mock.patch.object(main.catalog, "stale", return_value=False), \
                    mock.patch.object(main.catalog, "load", return_value={}):
                asyncio.run(main.Bot.on_ready(bot))
            self.assertEqual(bot.perms.soon.call_count, times, "once with the pump; a reconnect: the role events say")


class Events(unittest.TestCase):
    """The bot's role changed: looked at again, in the background (main.Bot.on_guild_role_update)."""
    def test_a_role_of_the_bots(self):
        from bot import main
        ran = []
        role = types.SimpleNamespace(permissions=1)
        guild = types.SimpleNamespace(id=GUILD, me=types.SimpleNamespace(roles=[role]))
        bot = types.SimpleNamespace(perms=types.SimpleNamespace(soon=lambda: ran.append(1)), user=types.SimpleNamespace(id=3))
        with mock.patch.object(config, "GUILD_ID", GUILD):
            after = types.SimpleNamespace(permissions=9, guild=guild)
            guild.me.roles = [after]
            asyncio.run(main.Bot.on_guild_role_update(bot, role, after))
            self.assertEqual(ran, [1])
            asyncio.run(main.Bot.on_guild_role_update(bot, after, after))
            self.assertEqual(ran, [1], "permissions the same: nothing")
            other = types.SimpleNamespace(permissions=8, guild=guild)
            asyncio.run(main.Bot.on_guild_role_update(bot, role, other))
            self.assertEqual(ran, [1], "not a role of the bot's")
        asyncio.run(main.Bot.on_member_update(bot, types.SimpleNamespace(roles=[1]), types.SimpleNamespace(id=3, roles=[1, 2])))
        asyncio.run(main.Bot.on_member_update(bot, types.SimpleNamespace(roles=[1]), types.SimpleNamespace(id=4, roles=[2])))
        self.assertEqual(ran, [1, 1])


if __name__ == "__main__":
    unittest.main()
