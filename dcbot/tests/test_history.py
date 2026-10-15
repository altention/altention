"""A conversation's history posted when a channel takes it up (bot/history.py)."""
import asyncio
import json
import os
import tempfile
import unittest

from bot import history
from tests import tmuxguard  # noqa: F401,E402  (no test reaches a real tmux server)


def transcript(path, turns):
    with open(path, "w") as f:
        for i in range(turns):
            f.write(json.dumps({"type": "user", "origin": {"kind": "human"}, "timestamp": "2026-10-01T01:00:00Z",
                                "message": {"content": "question %d" % i}}, separators=(",", ":")) + "\n")
            f.write(json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "answer %d" % i}]}}, separators=(",", ":")) + "\n")


class Thread(object):
    def __init__(self):
        self.sent = []

    async def send(self, text, silent=False):
        self.sent.append(text)


class Msg(object):
    def __init__(self, ch):
        self.ch = ch

    async def create_thread(self, name, auto_archive_duration=60):
        self.ch.thread = Thread()
        return self.ch.thread


class Ch(object):
    def __init__(self):
        self.sent, self.files, self.thread = [], [], None

    async def send(self, text, file=None, silent=False):
        self.sent.append(text)
        if file is not None:
            self.files.append(file.filename)
        return Msg(self)


class Bot(object):
    def __init__(self):
        self.backfilled = []

    def save(self):
        pass

    async def backfill(self, ch, messages):
        self.backfilled.append(len(messages))


class History(unittest.TestCase):
    def test_short_inline_long_in_a_thread_and_once_only(self):
        async def go():
            with tempfile.TemporaryDirectory() as d:
                path = os.path.join(d, "s.jsonl")
                transcript(path, 2)
                bot, ch, st = Bot(), Ch(), {}
                self.assertTrue(await history.show(bot, ch, st, "aaaaaaaa-1", path))
                await asyncio.sleep(0)
                self.assertEqual(bot.backfilled, [4])
                self.assertTrue(await history.show(bot, ch, st, "aaaaaaaa-1", path))
                self.assertIn("之前貼過", ch.sent[-1])
                transcript(path, 40)  # long ones too: all in the channel
                ch2, st2 = Ch(), {}
                self.assertTrue(await history.show(bot, ch2, st2, "bbbbbbbb-2", path))
                await asyncio.sleep(0)
                self.assertEqual(bot.backfilled, [4, 80])
                self.assertFalse(await history.show(bot, ch2, st2, "c", os.path.join(d, "missing.jsonl")))
        asyncio.run(go())


if __name__ == "__main__":
    unittest.main()
