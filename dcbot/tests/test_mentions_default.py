"""Messaging review P0-1: the bot's messages ping nobody unless a send says so, and only bot/calls.py says so (the
owner alone). What discord.py itself sends (handle_message_parameters, the same call Messageable.send makes) from the
client's default and a send's own allowed_mentions."""
import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))))
from altention import testing as _testing  # noqa: E402
_testing.guard_home()  # tests never touch the real home (altention/testing.py)
import glob
import os
import re
import unittest
from unittest import mock

import discord
from discord.http import handle_message_parameters

from bot import calls, config, main
from tests import tmuxguard  # noqa: F401  (no test reaches a real tmux server)

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def sent(text, **kw):
    """The allowed_mentions discord.py puts in the request for a send with this client's default."""
    client = discord.Client(intents=discord.Intents.default(), **main.client_options())
    params = handle_message_parameters(text, previous_allowed_mentions=client.allowed_mentions, **kw)
    return params.payload.get("allowed_mentions")


class Default(unittest.TestCase):
    def test_a_send_that_says_nothing_pings_nobody(self):
        """Claude's reply quoting <@42>, @everyone or a role: nobody pinged."""
        self.assertEqual(sent("hi <@42> <@&7> @everyone"), {"parse": []})
        ref = discord.MessageReference(message_id=5, channel_id=6)
        self.assertEqual(sent("a reply <@42>", message_reference=ref), {"parse": []},
                         "a reply does not ping its author either: Discord's replied_user is false when not given")

    def test_calls_allowed_pings_the_owner_alone(self):
        with mock.patch.object(config, "OWNER_ID", 42):
            got = sent("在等你回答 <@42> <@43> @everyone", allowed_mentions=calls.allowed())
        self.assertEqual(got, {"parse": [], "users": [42]})

    def test_no_owner_set_pings_nobody_even_through_calls(self):
        with mock.patch.object(config, "OWNER_ID", 0):
            got = sent("x", allowed_mentions=calls.allowed())
        self.assertEqual(got.get("users", []), [])
        self.assertEqual(got.get("parse"), [])

    def test_nothing_but_calls_asks_for_a_ping(self):
        """Only bot/calls.py builds an AllowedMentions that lets anyone through; nothing anywhere allows them all."""
        for path in glob.glob(os.path.join(HERE, "bot", "*.py")):
            with open(path, encoding="utf-8") as f:
                src = f.read()
            self.assertNotRegex(src, r"AllowedMentions\.all\(|allowed_mentions\s*=\s*None", path)
            if os.path.basename(path) != "calls.py":
                self.assertEqual(re.findall(r"AllowedMentions\((?!\))", src), [], path)


if __name__ == "__main__":
    unittest.main()
