"""Messaging review P0-1: the bot's messages ping nobody unless a send says so, and only bot/mentions.owner_only()
says so (the owner alone: a long turn's footer, the talk limit's ⏸️). What discord.py itself sends
(handle_message_parameters, the same call Messageable.send makes) from the client's default and a send's own."""
import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))))
from altention import testing as _testing  # noqa: E402
_testing.guard_home()  # tests never touch the real home (altention/testing.py)
import glob
import inspect
import os
import re
import unittest
from unittest import mock

import discord
from discord.http import handle_message_parameters

from bot import config, main, mentions
from tests import tmuxguard  # noqa: F401  (no test reaches a real tmux server)

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def sent(text, **kw):
    """The allowed_mentions discord.py puts in the request for a send, with the bot's default."""
    client = discord.Client(intents=discord.Intents.default(), allowed_mentions=mentions.nobody())
    params = handle_message_parameters(text, previous_allowed_mentions=client.allowed_mentions, **kw)
    return params.payload.get("allowed_mentions")


class Default(unittest.TestCase):
    def test_the_bot_starts_with_it(self):
        self.assertIn("allowed_mentions=mentions.nobody()", inspect.getsource(main.Bot.__init__))

    def test_a_send_that_says_nothing_pings_nobody(self):
        """Claude's reply quoting <@42>, @everyone or a role: nobody pinged."""
        self.assertEqual(sent("hi <@42> <@&7> @everyone"), {"parse": []})
        ref = discord.MessageReference(message_id=5, channel_id=6)
        self.assertEqual(sent("a reply <@42>", message_reference=ref), {"parse": []},
                         "a reply does not ping its author either: Discord's replied_user is false when not given")

    def test_owner_only_pings_the_owner_alone(self):
        with mock.patch.object(config, "OWNER_ID", 42):
            got = sent("⏸️ <@42> <@43> @everyone", allowed_mentions=mentions.owner_only())
        self.assertEqual(got, {"parse": [], "users": [42]})
        with mock.patch.object(config, "OWNER_ID", 0):
            got = sent("x", allowed_mentions=mentions.owner_only())
        self.assertEqual((got.get("parse"), got.get("users", [])), ([], []))

    def test_nothing_but_mentions_asks_for_a_ping(self):
        """Only bot/mentions.py builds an AllowedMentions that lets anyone through; nothing anywhere allows them all."""
        for path in glob.glob(os.path.join(HERE, "bot", "*.py")):
            with open(path, encoding="utf-8") as f:
                src = f.read()
            self.assertNotRegex(src, r"AllowedMentions\.all\(|allowed_mentions\s*=\s*None", path)
            if os.path.basename(path) != "mentions.py":
                self.assertEqual(re.findall(r"AllowedMentions\((?!\))", src), [], path)


if __name__ == "__main__":
    unittest.main()
