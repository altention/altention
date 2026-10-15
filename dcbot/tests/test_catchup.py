"""Messages sent while the bot was down are delivered after it reconnects, once."""
import asyncio
import datetime
import os
import tempfile
import unittest
from unittest import mock

from bot import catchup, config
from tests import tmuxguard  # noqa: F401,E402  (no test reaches a real tmux server)


class Author(object):
    def __init__(self, uid):
        self.id, self.bot = uid, False


class Msg(object):
    def __init__(self, mid, ch, age, uid=None):
        self.id, self.channel = mid, ch
        self.author = Author(uid if uid is not None else config.OWNER_ID)
        self.created_at = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(seconds=age)
        self.jump_url = "https://discord.com/x/%d" % mid


class Chan(object):
    def __init__(self, cid):
        self.id, self.history_msgs, self.sent = cid, [], []

    def history(self, after=None, limit=None, oldest_first=True):
        async def gen():
            for m in self.history_msgs:
                if m.id > after.id:
                    yield m
        return gen()

    async def send(self, text, silent=False):
        self.sent.append(text)


class Bot(object):
    def __init__(self, chans):
        self.chans, self.delivered, self.audits = chans, [], []

    def get_channel(self, cid):
        return self.chans.get(cid)

    async def on_message(self, m):
        if self.c.mark(m):
            self.delivered.append(m.id)

    async def audit(self, text):
        self.audits.append(text)


class Catchup(unittest.TestCase):
    def test_missed_messages_delivered_once_and_old_ones_pointed_out(self):
        tmp = tempfile.mkdtemp()
        with mock.patch.object(catchup, "SEEN", os.path.join(tmp, "seen.json")):
            ch = Chan(1)
            bot = Bot({1: ch})
            catchup.Catchup(bot).mark(Msg(10, ch, 100))  # before the restart
            c = bot.c = catchup.Catchup(bot)
            ch.history_msgs = [Msg(5, ch, 9000), Msg(11, ch, 4000), Msg(12, ch, 30), Msg(13, ch, 20, uid=999), Msg(14, ch, 10)]
            c.mark(ch.history_msgs[-1])  # arrived live before the catch-up read the history
            asyncio.run(c.run())
            self.assertEqual(bot.delivered, [12])
            self.assertIn("超過", ch.sent[0])
            self.assertIn("補送", ch.sent[1])
            again = catchup.Catchup(bot)
            self.assertEqual(again.seen["1"], 14, "remembered across restarts")
            asyncio.run(c.run())
            self.assertEqual(bot.delivered, [12], "only once per process")


if __name__ == "__main__":
    unittest.main()
