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
    def __init__(self, mid, ch, age, uid=None, content="hello"):
        self.id, self.channel, self.content = mid, ch, content
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


class Held(unittest.TestCase):
    """C-124: a catch-up hands on plain messages and the commands that only show (REPLAYED). Any other command is
    not done, each said in the channel: a !restart sent before a restart restarted the bot once more."""

    def run_with(self, contents, kind=None):
        tmp = tempfile.mkdtemp()
        with mock.patch.object(catchup, "SEEN", os.path.join(tmp, "seen.json")):
            ch = Chan(1)
            bot = Bot({1: ch})
            bot.channels_state = {"1": {"kind": kind}} if kind else {}
            catchup.Catchup(bot).mark(Msg(10, ch, 100))
            c = bot.c = catchup.Catchup(bot)
            ch.history_msgs = [Msg(11 + i, ch, 60 - i, content=t) for i, t in enumerate(contents)]
            asyncio.run(c.run())
            later = bot.c = catchup.Catchup(bot)  # the next start: nothing of these again
            asyncio.run(later.run())
        by_id = {11 + i: t for i, t in enumerate(contents)}
        return [by_id[i] for i in bot.delivered], ch.sent, bot.audits

    def test_only_plain_messages_and_what_only_shows(self):
        delivered, sent, audits = self.run_with([
            "hello", "!restart", "!status", "!cwd /tmp", "/clear", "/btw what now?", "/tmp/x 是什麼？", "!key Enter",
            "!rollback", "please do not !restart yet", "!help", "!recap 2", "!screen", "!ls -la", "/model sonnet",
            "!!先停一下，改用 B 方案"])
        self.assertEqual(delivered, ["hello", "!status", "/btw what now?", "/tmp/x 是什麼？", "please do not !restart yet",
                                     "!help", "!recap 2", "!screen", "!!先停一下，改用 B 方案"],
                         "a message read at once (!!…) is a plain message (#code-review on e76fa01)")
        lines = [t for t in sent if t.startswith("-# ⏸️")]
        self.assertEqual(lines, ["-# ⏸️ 離線時收到的 7 則沒有執行，要的話再送一次：`!restart`、`!cwd /tmp`、`/clear`、"
                                 "`!key Enter`、`!rollback`…"], "many: one message, the first few and how many (manager)")
        self.assertIn("補送機器人重啟期間你發的 9 則訊息", "".join(sent))
        self.assertEqual(len(audits), 1)
        self.assertIn("其中 7 則指令沒有執行", audits[0])

    def test_a_restart_sent_before_the_restart_is_not_done_twice(self):
        delivered, sent, _ = self.run_with(["!restart"])
        self.assertEqual(delivered, [])
        self.assertEqual(sent, ["-# ⏸️ 離線時收到的 `!restart` 沒有執行，要的話再送一次。"], "no 補送 line for none")

    def test_a_few_are_said_one_line_each(self):
        _, sent, _ = self.run_with(["!restart", "hello", "!key Enter", "/clear"])
        self.assertEqual([t for t in sent if t.startswith("-# ⏸️")],
                         ["-# ⏸️ 離線時收到的 `%s` 沒有執行，要的話再送一次。" % t for t in ("!restart", "!key Enter", "/clear")])

    def test_a_terminal_channel_runs_nothing_again(self):
        """Manager 2026-11-03: a terminal channel's shell runs every message (`rm -rf build`, `git push`): a
        catch-up runs none of them, only what only shows."""
        delivered, sent, audits = self.run_with(["rm -rf build", "!status", "git push", "!!", "ls"], kind="terminal")
        self.assertEqual(delivered, ["!status"])
        self.assertEqual([t for t in sent if t.startswith("-# ⏸️")],
                         ["-# ⏸️ 離線時收到的 4 則沒有執行，要的話再送一次：`rm -rf build`、`git push`、`!!`、`ls`"])
        self.assertIn("其中 4 則指令沒有執行", audits[0])
        delivered, sent, _ = self.run_with(["make test"], kind="terminal")
        self.assertEqual((delivered, sent[-1]), ([], "-# ⏸️ 離線時收到的 `make test` 沒有執行，要的話再送一次。"))
        delivered, _, _ = self.run_with(["rm -rf build"])  # the same words in a Claude channel: a message to Claude
        self.assertEqual(delivered, ["rm -rf build"])

    def test_every_command_of_the_bot_but_the_listed_is_held(self):
        """The list is what is replayed: a command added to the bot later is held until it is put on it."""
        from bot.main import BUILTINS
        for cmd in BUILTINS:
            m = Msg(1, None, 0, content=cmd + " x")
            self.assertEqual(catchup.held(m) is None, cmd in catchup.REPLAYED, cmd)
        self.assertIsNotNone(catchup.held(Msg(1, None, 0, content="!newcommand")))
        for plain in ("", "   ", "hi !restart", "/ alone", "/tmp/a b"):
            self.assertIsNone(catchup.held(Msg(1, None, 0, content=plain)), plain)


if __name__ == "__main__":
    unittest.main()
