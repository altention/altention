"""Messages sent while the bot was down are delivered after it reconnects, once."""
import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))))
from altention import testing as _testing  # noqa: E402
_testing.guard_home()  # tests never touch the real home (altention/testing.py; 11-08: one left a real dcremote-owner)
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
            self.assertEqual(bot.delivered, [12], "nothing owed until the connection is lost again")


class NewChannel(unittest.TestCase):
    """manager 11-08: a channel made a few minutes before, its first message from the owner just as the bot went down
    (seen.json has no message of it yet), is caught up too, from when the bot went down; an old channel never written
    in is not read at every start."""

    def test_the_first_message_of_a_new_channel_during_a_restart(self):

        import discord
        tmp = tempfile.mkdtemp()
        now = datetime.datetime.now(datetime.timezone.utc)
        down = now - datetime.timedelta(seconds=30)
        snow = lambda t: discord.utils.time_snowflake(t)  # noqa: E731
        new_id = snow(down - datetime.timedelta(minutes=7))       # made 7 minutes before
        old_id = snow(down - datetime.timedelta(days=3))          # made 3 days before, never written in
        with open(os.path.join(tmp, "heartbeat"), "w") as f:
            f.write("x")
        os.utime(os.path.join(tmp, "heartbeat"), (down.timestamp(), down.timestamp()))
        with mock.patch.object(catchup, "SEEN", os.path.join(tmp, "seen.json")), mock.patch.object(config, "STATE", tmp):
            new, old = Chan(new_id), Chan(old_id)
            bot = Bot({new_id: new, old_id: old})
            bot.channels_state = {str(new_id): {"kind": "conversation"}, str(old_id): {"kind": "conversation"}}
            c = bot.c = catchup.Catchup(bot)
            first = Msg(snow(down + datetime.timedelta(seconds=1)), new, 29)
            new.history_msgs = [first]
            read = []
            real = old.history
            old.history = lambda **kw: (read.append(1), real(**kw))[1]
            asyncio.run(c.run())
        self.assertEqual(bot.delivered, [first.id], "the new channel's first message caught up")
        self.assertEqual(read, [], "the old channel never written in is not read")



class Reconnect(unittest.TestCase):
    """C-170: a network outage longer than Discord keeps a session: the gateway opens a new one (on_ready again)
    and Discord replays nothing. What the owner said meanwhile is caught up like after a restart."""

    def setUp(self):
        tmp = tempfile.mkdtemp()
        patch = mock.patch.object(catchup, "SEEN", os.path.join(tmp, "seen.json"))
        patch.start()
        self.addCleanup(patch.stop)
        self.ch = Chan(1)
        self.bot = Bot({1: self.ch})
        catchup.Catchup(self.bot).mark(Msg(10, self.ch, 100))
        self.c = self.bot.c = catchup.Catchup(self.bot)
        asyncio.run(self.c.run())  # the first on_ready: nothing missed
        self.bot.on_message_live = lambda m: asyncio.run(self.bot.on_message(m))

    def test_what_was_said_while_cut_off_is_delivered_once(self):
        self.bot.on_message_live(Msg(11, self.ch, 600))  # live, before the outage
        self.c.cut()  # on_disconnect
        self.ch.history_msgs = [Msg(11, self.ch, 600), Msg(12, self.ch, 300), Msg(13, self.ch, 200, uid=999),
                                Msg(14, self.ch, 100), Msg(15, self.ch, 5)]
        self.bot.on_message_live(self.ch.history_msgs[-1])  # live on the new session, before the catch-up read
        asyncio.run(self.c.run())  # on_ready of the new session
        self.assertEqual(self.bot.delivered, [11, 15, 12, 14])
        self.assertIn("補送", self.ch.sent[-1])
        asyncio.run(self.c.run())
        self.assertEqual(self.bot.delivered, [11, 15, 12, 14], "each once")

    def test_dropped_again_before_on_ready_keeps_the_older_point(self):
        self.c.cut()
        self.bot.on_message_live(Msg(13, self.ch, 100))  # moves seen on before the second drop (#code-review)
        self.c.cut()  # the reconnect failed and the socket closed again
        self.ch.history_msgs = [Msg(12, self.ch, 300), Msg(13, self.ch, 100)]
        asyncio.run(self.c.run())
        self.assertEqual(self.bot.delivered, [13, 12], "12, said before the first drop's point, is not missed")

    def test_a_resumed_session_owes_nothing(self):
        """Discord replays a resumed session's events: no catch-up, and none left waiting for a later on_ready
        (it would read messages handled long ago as missed)."""
        self.c.cut()
        self.c.resumed()
        self.ch.history_msgs = [Msg(12, self.ch, 4000)]
        asyncio.run(self.c.run())
        self.assertEqual((self.bot.delivered, self.ch.sent), ([], []))

    def test_the_window_still_holds(self):
        self.c.cut()
        self.ch.history_msgs = [Msg(12, self.ch, catchup.WINDOW + 60), Msg(13, self.ch, 60)]
        asyncio.run(self.c.run())
        self.assertEqual(self.bot.delivered, [13])
        self.assertIn("超過", self.ch.sent[0])

    def test_a_second_catch_up_waits_for_the_first(self):
        """A drop and a new session while a catch-up still reads the history: the second waits for the first
        (the lock), so the older messages are handed on first."""
        self.c.cut()
        self.ch.history_msgs = [Msg(12, self.ch, 60), Msg(13, self.ch, 50), Msg(14, self.ch, 30)]
        gate, calls, plain = asyncio.Event(), [], self.ch.history

        def history(after=None, limit=None, oldest_first=True):
            calls.append(after.id)
            first = len(calls) == 1

            async def gen():
                if first:
                    await gate.wait()
                async for m in plain(after=after, limit=limit, oldest_first=oldest_first):
                    yield m
            return gen()
        self.ch.history = history

        let_run = asyncio.sleep  # (the catch-up's own pauses are patched away below)

        async def both():
            a = asyncio.ensure_future(self.c.run())
            for _ in range(5):
                await let_run(0)  # a reads the history and waits there
            await self.bot.on_message(self.ch.history_msgs[1])  # 13, live, before the next drop
            self.c.cut()
            b = asyncio.ensure_future(self.c.run())
            for _ in range(5):
                await let_run(0)  # b, without the lock, would read and hand on 14 now
            gate.set()
            await asyncio.gather(a, b)
        with mock.patch.object(catchup.asyncio, "sleep", mock.AsyncMock()):
            asyncio.run(both())
        self.assertEqual(self.bot.delivered, [13, 12, 14])


class OnReady(unittest.TestCase):
    """main.Bot: every on_ready starts a catch-up (not only the first, which also starts the pump); a lost
    connection cuts, a resumed one drops the cut."""

    def stub(self, pumping):
        from bot import main
        bot = mock.Mock()
        bot.pumping = pumping
        bot.get_guild.return_value = None
        bot.audit = mock.AsyncMock()
        bot.sync_menu = mock.AsyncMock()
        bot.loop.create_task.side_effect = lambda coro: coro.close()
        bot.catchup = mock.Mock()
        return main, bot

    def test_every_on_ready_catches_up(self):
        for pumping in (False, True):
            main, bot = self.stub(pumping)
            with mock.patch.object(main, "git", return_value="abc"), \
                    mock.patch.object(main, "NOTICE", "/nonexistent/notice"), \
                    mock.patch.object(main, "RESTART_FLAG", "/nonexistent/flag"), \
                    mock.patch.object(main.catalog, "stale", return_value=False), \
                    mock.patch.object(main.catalog, "load", return_value={}):
                asyncio.run(main.Bot.on_ready(bot))
            bot.catchup.run.assert_called_once_with()

    def test_disconnect_cuts_and_resume_drops_it(self):
        main, bot = self.stub(True)
        asyncio.run(main.Bot.on_disconnect(bot))
        bot.catchup.cut.assert_called_once_with()
        asyncio.run(main.Bot.on_resumed(bot))
        bot.catchup.resumed.assert_called_once_with()


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
