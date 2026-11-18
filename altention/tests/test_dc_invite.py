"""C-191: the permissions the bot asks for (altention/dc/invite.py): the 14 its features use and Administrator in a new
installation's link; with the server's ID the same link changes them in place; without Administrator, the way back."""
import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))))
from altention import testing as _testing  # noqa: E402
_testing.guard_home()  # tests never touch the real home (altention/testing.py)
import unittest
import urllib.parse

from altention.dc import invite


def query(url):
    return urllib.parse.parse_qs(urllib.parse.urlparse(url).query)


class Invite(unittest.TestCase):
    def test_the_14_and_administrator(self):
        self.assertEqual(len(invite.PERMISSIONS), 14)
        self.assertEqual(invite.PERMISSION_BITS, 2252128378809552)
        self.assertEqual(invite.WITH_ADMIN, 2252128378809560, "the 14 stay in it: Administrator taken away later, the bot as before")
        q = query(invite.url("123"))
        self.assertEqual((q["client_id"], q["scope"], q["permissions"], q["integration_type"]),
                         (["123"], ["bot applications.commands"], ["2252128378809560"], ["0"]))
        self.assertNotIn("guild_id", q)

    def test_on_a_server_and_the_way_back(self):
        self.assertEqual(query(invite.url("123", 9))["guild_id"], ["9"])
        self.assertEqual(query(invite.url("123", 9, admin=False))["permissions"], ["2252128378809552"])

    def test_what_is_there_and_what_is_missing(self):
        self.assertTrue(invite.has_admin(8))
        self.assertFalse(invite.has_admin(invite.PERMISSION_BITS))
        self.assertFalse(invite.has_admin(None))
        self.assertEqual(invite.missing(invite.PERMISSION_BITS), [])
        self.assertEqual(invite.missing(8), [], "Administrator has them all")
        self.assertEqual(invite.missing(invite.PERMISSION_BITS & ~(1 << 51)), ["PIN_MESSAGES"])
        self.assertEqual(len(invite.missing(0)), 14)

    def test_the_installer_asks_the_same(self):
        _sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))), "ops"))
        import discordapp
        self.assertEqual((discordapp.PERMISSIONS, discordapp.PERMISSION_BITS), (invite.PERMISSIONS, invite.PERMISSION_BITS))
        self.assertEqual(discordapp.invite_url("123"), invite.url("123"))


if __name__ == "__main__":
    unittest.main()
