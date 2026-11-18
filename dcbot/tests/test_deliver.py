"""C-172a P1b: deliver(), the one way the bot's own messages go out (bot/deliver.py): what it is handed goes to
discord.py as it is, and the message goes into the ledger."""
import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))))
from altention import testing as _testing  # noqa: E402
_testing.guard_home()  # tests never touch the real home (altention/testing.py)
import asyncio
import os
import tempfile
import types
import unittest
from unittest import mock

from bot import deliver, ledger
from tests import tmuxguard  # noqa: F401  (no test reaches a real tmux server)


class Msg(object):
    def __init__(self, ch, mid):
        self.channel, self.id = ch, mid

    async def edit(self, *a, **kw):
        self.channel.log.append(("edit", self.id, a, kw))
        return self

    async def delete(self):
        self.channel.log.append(("delete", self.id))


class Ch(object):
    id = 9

    def __init__(self):
        self.log = []

    async def send(self, *a, **kw):
        self.log.append(("send", a, kw))
        return Msg(self, 100 + len(self.log))

    def get_partial_message(self, mid):
        return Msg(self, mid)


class Deliver(unittest.TestCase):
    def setUp(self):
        p = mock.patch.dict(os.environ, {"DCBOT_LEDGER": os.path.join(tempfile.mkdtemp(), "ledger.json")})
        p.start()
        self.addCleanup(p.stop)
        ledger.reset()
        self.addCleanup(ledger.reset)

    def test_sent_as_it_is_and_noted(self):
        ch, view = Ch(), object()
        msg = asyncio.run(deliver.deliver(ch, "hi", silent=True, view=view, intent="post", frm="#st-1", framed=("card",)))
        self.assertEqual(ch.log, [("send", ("hi",), {"silent": True, "view": view})], "nothing of the bot's own goes to Discord")
        self.assertEqual(msg.id, 101)
        e = ledger.entry(101, 9)
        self.assertEqual((e["intent"], e["from"], e["framed"]), ("post", "#st-1", ["card"]))

    def test_edited_by_id_or_message(self):
        ch = Ch()
        asyncio.run(deliver.deliver(ch, content="new", view=None, intent="board", key="看板", edit=55))
        asyncio.run(deliver.deliver(ch, content="newer", intent="card", edit=Msg(ch, 56)))
        self.assertEqual(ch.log, [("edit", 55, (), {"content": "new", "view": None}), ("edit", 56, (), {"content": "newer"})])
        self.assertEqual((ledger.entry(55, 9)["intent"], ledger.entry(55, 9)["key"]), ("board", "看板"))
        self.assertEqual(ledger.entry(56, 9)["intent"], "card")

    def test_an_edit_of_the_buttons_only_keeps_what_was_noted(self):
        """Both reviews on 99392598, manager 11-16: a menu's card closed (view=None, as menus.py does) keeps its key
        and framed; framed=() said clears it."""
        ch = Ch()
        asyncio.run(deliver.deliver(ch, "menu", intent="card", key="menu:t", framed=("card",)))
        asyncio.run(deliver.deliver(ch, view=None, intent="card", edit=101))
        self.assertEqual((ledger.entry(101, 9)["key"], ledger.entry(101, 9)["framed"]), ("menu:t", ["card"]))
        asyncio.run(deliver.deliver(ch, view=None, intent="card", framed=(), edit=101))
        self.assertEqual(ledger.entry(101, 9)["framed"], [])

    def test_audit_and_a_backfill_are_not_kept(self):
        """manager 11-16 (#code-review's notes on 99392598): #audit lines and a backfill go out, unrecorded."""
        from bot import main
        ch = Ch()
        bot = types.SimpleNamespace(audit_ch=ch)
        asyncio.run(main.Bot.audit(bot, "a line"))
        asyncio.run(main.Bot.backfill(bot, ch, ["one", "two"]))
        self.assertEqual(len(ch.log), 5)
        self.assertEqual(ledger._loaded()["channels"], {})

    def test_removed(self):
        ch = Ch()
        asyncio.run(deliver.remove(ch, 7))
        asyncio.run(deliver.remove(None, Msg(ch, 8)))
        self.assertEqual(ch.log, [("delete", 7), ("delete", 8)])

    def test_what_discord_refuses_is_raised_as_before(self):
        class Refusing(Ch):
            async def send(self, *a, **kw):
                raise RuntimeError("50035")
        with self.assertRaises(RuntimeError):
            asyncio.run(deliver.deliver(Refusing(), "x", intent="status"))

    def test_a_channel_without_an_id(self):
        thread = types.SimpleNamespace(send=Ch().send)  # (its message's channel says where it went)
        msg = asyncio.run(deliver.deliver(thread, "x", intent="status"))
        self.assertEqual(ledger.entry(msg.id, 9)["intent"], "status")


class NotKept(unittest.TestCase):
    """#code-review on ed28927e, manager 11-16: 處理中 (edited every 5 seconds, then deleted) is not kept: a turn adds
    nothing to the ledger, and many updates of it write nothing; a deleted message's entry goes; an edit that changes
    nothing on record writes nothing."""
    from tests.test_turn_end import TurnEnd as _T
    ev, tearDown = _T.ev, _T.tearDown  # (its setUp sets config.INTERIM and the like: put back after each)

    def setUp(self):
        self._T.setUp(self)
        p = mock.patch.dict(os.environ, {"DCBOT_LEDGER": os.path.join(tempfile.mkdtemp(), "ledger.json")})
        p.start()
        self.addCleanup(p.stop)
        ledger.reset()
        self.addCleanup(ledger.reset)

    @staticmethod
    def count():
        return sum(len(r) for r in ledger._loaded()["channels"].values())

    def test_a_turn_adds_nothing_and_progress_writes_nothing(self):
        import time
        saves = []
        with mock.patch.object(ledger, "_soon", lambda: saves.append(1)):
            async def go():
                await self.bridge.handle(self.ev("UserPromptSubmit", prompt="go"))
                turn = self.bridge.rt(self.ch).turn
                turn.t0 -= 600
                before = (self.count(), len(saves))
                for i in range(6):  # (each one 5 seconds after the last, as the lane would run them)
                    turn.lines.append("Bash step %d" % i)
                    turn.last = time.time() - 6
                    await self.bridge.progress(self.ch, turn, self.bridge.lane(self.ch))
                self.assertIsNotNone(turn.msg, "the message is there, edited")
                self.assertEqual((self.count(), len(saves)), before, "N updates: no entry, no write")
                await self.bridge.handle(self.ev("Stop", last_assistant_message="done"))
                return before
            before = asyncio.run(go())
        self.assertEqual(self.count(), before[0] + 1, "a turn: its reply only, 處理中 gone with nothing left")

    def test_removed_goes_and_an_unchanged_edit_writes_nothing(self):
        ch = Ch()
        msg = asyncio.run(deliver.deliver(ch, "x", intent="status"))
        self.assertIsNotNone(ledger.entry(msg.id, 9))
        asyncio.run(deliver.remove(ch, msg))
        self.assertIsNone(ledger.entry(msg.id, 9))
        asyncio.run(deliver.deliver(ch, content="a", intent="card", key="k", edit=60))
        ledger.save()
        saves = []
        with mock.patch.object(ledger, "_soon", lambda: saves.append(1)):
            asyncio.run(deliver.deliver(ch, content="b", intent="card", key="k", edit=60))
            self.assertEqual(saves, [], "the same on record: nothing written")
            asyncio.run(deliver.deliver(ch, content="c", intent="post", key="k", edit=60))
            self.assertEqual(saves, [1])
        asyncio.run(deliver.remove(ch, 61))  # (by id: not on record, nothing to do)

    def test_a_message_gone_already_loses_its_entry_and_says_so(self):
        """#code-review-2 on 09b745d0: deleted by hand (Discord's NotFound): its entry goes, and the caller still
        hears it, as before."""
        class NotFound(Exception):
            pass

        class Gone(Msg):
            async def delete(self):
                raise NotFound("10008")
        ch = Ch()
        asyncio.run(deliver.deliver(ch, content="x", intent="board", key="k", edit=70))
        with self.assertRaises(NotFound):
            asyncio.run(deliver.remove(ch, Gone(ch, 70)))
        self.assertIsNone(ledger.entry(70, 9))


if __name__ == "__main__":
    unittest.main()
