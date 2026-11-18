"""C-201 6.2: the bot keeps #audit by its ID (its channel state: system "audit", plain), so a #audit renamed in
Discord is still the one at the next start, not a second #audit made by the name; a renamed one is no new channel
asking for setup; the rename is said there. The engine's own lines go to @audit (the bot's), not to a name."""
import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))))
from altention import testing as _testing  # noqa: E402
_testing.guard_home()  # tests never touch the real home (altention/testing.py; 11-08: one left a real dcremote-owner)
import os
import unittest
from unittest import mock

from bot import config
from tests import tmuxguard  # noqa: F401,E402  (no test reaches a real tmux server)
from tests.test_setup_channel import Ch, bot, run

ENGINE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class KeptByItsId(unittest.TestCase):
    def start(self, b):
        botmain = __import__("bot.main", fromlist=["main"])
        b.new_install, b.audit_ch = False, None
        with mock.patch.object(botmain, "setup_done", lambda: True):
            run(b.own_channels(b.guild))
        return b.audit_ch

    def test_a_renamed_audit_is_still_the_one(self):
        _, b = bot()
        old = Ch(b.guild, config.AUDIT_CHANNEL, 1001)
        b.guild.text_channels.append(old)
        self.assertIs(self.start(b), old)
        st = b.channels_state["1001"]
        self.assertEqual((st["system"], st["plain"], st["configured"]), ("audit", True, True))
        old.name = "audit-old"  # renamed in Discord
        self.assertIs(self.start(b), old)
        self.assertEqual([c.name for c in b.guild.text_channels], ["audit-old"], "no second #audit")
        self.assertFalse(b.needs_setup(old, b.channels_state["1001"]))
        self.assertTrue(b.is_audit(old))

    def test_found_by_name_when_its_record_is_gone(self):
        _, b = bot({"1001": {"system": "audit", "plain": True, "configured": True, "cwd": "/w"}})  # deleted since
        self.assertEqual(self.start(b).name, config.AUDIT_CHANNEL)  # made by the name, as before
        made = b.audit_ch
        self.assertEqual(b.channels_state[str(made.id)]["system"], "audit")
        self.assertNotIn("system", b.channels_state["1001"], "one record of #audit")
        mine = Ch(b.guild, config.AUDIT_CHANNEL, 1002)  # the owner names another channel audit: not the bot's then
        self.assertFalse(b.is_audit(mine))
        self.assertTrue(b.is_audit(made))

    def test_a_new_installation_keeps_the_one_it_made(self):
        botmain, b = bot()
        with mock.patch.object(botmain, "setup_done", lambda: True), \
                mock.patch.object(botmain.Bot, "first_chat", lambda self, g: _none()):
            run(b.own_channels(b.guild))
        self.assertEqual(b.channels_state[str(b.audit_ch.id)]["system"], "audit")

    def test_the_rename_is_said_there(self):
        _, b = bot()
        old = Ch(b.guild, config.AUDIT_CHANNEL, 1001)
        b.guild.text_channels.append(old)
        self.start(b)
        before, after = type("C", (), {"name": "audit", "id": 1001})(), type("C", (), {"name": "紀錄", "id": 1001})()
        run(b.on_guild_channel_update(before, after))
        self.assertEqual(b.audits, ["#audit 改名為 #紀錄：機器人照樣記在這個頻道（認的是頻道 ID），不會另開一個"])


async def _none():
    return None


class TheEngineSaysItToAtAudit(unittest.TestCase):
    """Every engine line for #audit goes to @audit, which the bot reads as the channel it keeps (posts.channel)."""

    def test_no_engine_code_names_audit_by_its_setting(self):
        said = []
        for rel in ("altention/botmove.py", "altention/secret_store.py", "ops/setup.py", "status/boards.py"):
            with open(os.path.join(ENGINE, rel), encoding="utf-8") as f:
                text = f.read()
            if 'config.setting("dcbot.channels.audit"' in text:
                said.append(rel)
            self.assertIn('"@audit"', text, rel)
        self.assertEqual(said, [])

    def test_at_audit_is_the_channel_the_bot_keeps(self):
        from bot import posts
        _, b = bot()
        b.audit_ch = Ch(b.guild, "renamed", 1001)
        p = posts.Posts.__new__(posts.Posts)
        p.bot = b
        self.assertIs(run(p.channel("@audit")), b.audit_ch)


if __name__ == "__main__":
    unittest.main()
