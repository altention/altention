"""state/waiting-on-owner.json (3b 2.6d): what waits for the owner, as of now: the menus shown, the questions and
plans still open. Written from what the bot holds whenever one of those changes; every way one opens or closes is
kept here, one by one (a state file's every writer)."""
import asyncio
import contextlib
import io
import json
import os
import tempfile
import time
import unittest
from unittest import mock

from bot import ask, awaiting, config, menus
from tests import tmuxguard  # noqa: F401,E402  (no test reaches a real tmux server)
from tests.test_ask import Q1, Q2, Click, FakeChannel


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = os.path.join(self.tmp.name, "state")
        patches = [mock.patch.object(config, "STATE", self.state), mock.patch.object(config, "OWNER_ID", 111),
                   mock.patch.object(ask, "ASKS", os.path.join(self.state, "asks")),
                   mock.patch.object(ask, "ANSWERS", os.path.join(self.state, "answers")),
                   mock.patch.object(awaiting, "menus", None), mock.patch.object(awaiting, "written", None),
                   mock.patch.object(awaiting, "on", True), mock.patch.object(awaiting, "failing", False)]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(self.tmp.cleanup)
        self.ch = FakeChannel()

        async def audit(text):
            pass
        self.asker = ask.Asker(audit)

    def whole(self):
        with open(os.path.join(self.state, awaiting.FILE), encoding="utf-8") as f:
            return json.load(f)

    def file(self):
        """The list; and the file is {"at": when written, "waiting": the list}, nothing else."""
        whole = self.whole()
        self.assertEqual(sorted(whole), ["at", "waiting"])
        self.assertAlmostEqual(whole["at"], time.time(), delta=30)
        return whole["waiting"]

    def kinds(self):
        self.assertEqual([n for n in os.listdir(self.state) if n.endswith(".tmp")], [], "written whole, renamed over")
        return [(w["channel"], w["kind"]) for w in self.file()]


class Questions(Base):
    def test_a_question_from_asked_to_answered(self):
        async def go():
            await self.asker.post(self.ch, {"ask_id": "7-1", "questions": [Q1, Q2]})
            self.assertEqual(self.kinds(), [("9", "question")])
            self.assertEqual(sorted(self.file()[0]), ["channel", "kind", "since"])
            self.assertAlmostEqual(self.file()[0]["since"], __import__("time").time(), delta=5)
            await self.asker.on_interaction(Click(self.ch, ask.custom_id("7-1", 0, "1")))
            self.assertEqual(self.kinds(), [("9", "question")], "one of its two answered: it still waits")
            await self.asker.on_interaction(Click(self.ch, ask.custom_id("7-1", 1, "s"), values=["0"]))
            self.assertEqual(self.kinds(), [])
        asyncio.run(go())

    def test_a_plan_from_shown_to_approved(self):
        async def go():
            await self.asker.post_plan(self.ch, {"ask_id": "7-2", "plan": "do it"})
            self.assertEqual(self.kinds(), [("9", "plan")])
            await self.asker.on_interaction(Click(self.ch, ask.custom_id("7-2", 0, "y")))
            self.assertEqual(self.kinds(), [])
        asyncio.run(go())

    def test_a_question_typed_an_answer_to(self):
        async def go():
            await self.asker.post(self.ch, {"ask_id": "7-1", "questions": [Q1]})
            click = Click(self.ch, ask.custom_id("7-1", 0, "o"))
            await self.asker.on_interaction(click)
            self.assertEqual(self.kinds(), [("9", "question")])
            if not await self.asker.typed(self.ch, "my own answer"):
                await self.asker.record(click, "7-1", 0, "my own answer")
            self.assertEqual(self.kinds(), [])
        asyncio.run(go())

    def test_the_turn_ended_or_was_stopped(self):
        async def go():
            await self.asker.post(self.ch, {"ask_id": "7-1", "questions": [Q1]})
            await self.asker.post_plan(self.ch, {"ask_id": "7-2", "plan": "p"})
            self.assertEqual(sorted(self.kinds()), [("9", "plan"), ("9", "question")])
            await self.asker.cancel(self.ch)
            self.assertEqual(self.kinds(), [])
        asyncio.run(go())

    def test_the_channel_was_deleted(self):
        async def go():
            await self.asker.post(self.ch, {"ask_id": "7-1", "questions": [Q1]})
            await self.asker.post(self.ch, {"ask_id": "7-3", "questions": [Q1], "host": "lap"})
            self.assertEqual(len(self.kinds()), 2)
            await self.asker.gone(self.ch)
            self.assertEqual(self.kinds(), [], "one let go at once; one closed and only owed to its machine: neither waits for the owner")
            self.assertEqual([n for n, _ in ask.waiting(owed=True)], ["7-3"])
        asyncio.run(go())

    def test_one_owed_to_another_machine_handed_over_or_given_up(self):
        async def go():
            for limit in (12 * 3600, -1):
                await self.asker.post(self.ch, {"ask_id": "7-3", "questions": [Q1], "host": "lap"})
                await self.asker.cancel(self.ch)
                self.assertEqual(self.kinds(), [])
                with mock.patch.object(ask, "deliver_to", mock.AsyncMock(return_value=True)):
                    self.assertEqual(await self.asker.again("lap", limit=limit), 0)
                self.assertEqual((self.kinds(), ask.waiting(owed=True)), ([], []))
        asyncio.run(go())

    def test_one_that_could_not_be_shown_never_waited(self):
        class Broken(FakeChannel):
            async def send(self, content, view=None):
                raise RuntimeError("discord is down")

        async def go():
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertFalse(await self.asker.post(Broken(), {"ask_id": "7-1", "questions": [Q1]}))
                self.assertFalse(await self.asker.post_plan(Broken(), {"ask_id": "7-2", "plan": "p"}))
            awaiting.note()
            self.assertEqual(self.kinds(), [])
        asyncio.run(go())

    def test_every_record_written_or_removed_goes_through_the_two_functions(self):
        """ask.save() and ask.forget() write the file; nothing else in bot/ or tools/ touches state/asks/."""
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, "bot", "ask.py"), encoding="utf-8") as f:
            source = f.read()
        self.assertEqual(source.count("_path(ASKS, ask_id)"), 4, "read(), save() twice, forget(): a new writer notes too")
        for folder in ("bot", "tools"):
            for name in sorted(os.listdir(os.path.join(root, folder))):
                path = os.path.join(root, folder, name)
                if not os.path.isfile(path) or name == "ask.py":
                    continue
                with open(path, encoding="utf-8", errors="replace") as f:
                    text = f.read()
                self.assertNotIn("ask.ASKS", text, name)
                self.assertNotIn('"asks"', text, name)


class Shown(Base):
    def menus(self):
        return menus.Menus(None)

    def test_every_way_a_menu_comes_or_goes(self):
        m = self.menus()
        awaiting.start()
        self.assertIsInstance(m.open, awaiting.Shown)
        self.assertIs(awaiting.menus, m.open)
        self.assertEqual(self.kinds(), [])
        m.open["1"] = {"channel": 9, "msg": 1, "title": "t"}
        self.assertEqual(self.kinds(), [("9", "menu")])
        self.assertIsInstance(self.file()[0]["since"], float)
        m.open.setdefault("2", {"channel": 8, "msg": 2, "title": "t", "since": 5.0})
        self.assertEqual(self.kinds(), [("8", "menu"), ("9", "menu")], "oldest first")
        m.open.update({"3": {"channel": 7, "msg": 3, "title": "t", "since": 6.0}})
        self.assertEqual(len(self.kinds()), 3)
        m.open.pop("3", None)
        self.assertEqual(len(self.kinds()), 2)
        m.open.pop("no such", None)
        del m.open["2"]
        self.assertEqual(self.kinds(), [("9", "menu")])
        m.open.popitem()
        self.assertEqual(self.kinds(), [])
        m.open["4"] = {"channel": 9, "msg": 1, "title": "t"}
        m.open.clear()
        self.assertEqual(self.kinds(), [])

    def test_the_session_moved_on_and_the_channel_was_deleted(self):
        m = self.menus()
        other = type("C", (), {"id": 8})()
        for close in ("resolve", "gone"):
            m.open["1"] = {"channel": 9, "msg": 100, "title": "t"}
            m.open["2"] = {"channel": 8, "msg": 2, "title": "t"}
            if close == "resolve":
                asyncio.run(m.resolve(self.ch))  # (its message is not there to edit: tried, and the menu is dropped all the same)
            else:
                m.gone(self.ch)
            self.assertEqual(self.kinds(), [("8", "menu")], close)
            m.gone(other)
            self.assertEqual(self.kinds(), [], close)

    def test_the_menus_module_holds_its_menus_nowhere_else(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, "bot", "menus.py"), encoding="utf-8") as f:
            source = f.read()
        self.assertEqual(source.count("self.open = "), 1)
        self.assertIn("self.bridge, self.open = bridge, awaiting.Shown()", source)
        with open(os.path.join(root, "bot", "main.py"), encoding="utf-8") as f:
            self.assertIn("self.tmux.menus.gone(channel)", f.read(), "a deleted channel's menus are dropped")

    def test_a_menu_and_a_question_together(self):
        m = self.menus()

        async def go():
            await self.asker.post(self.ch, {"ask_id": "7-1", "questions": [Q1]})
            m.open["1"] = {"channel": 9, "msg": 1, "title": "t"}
            self.assertEqual(sorted(self.kinds()), [("9", "menu"), ("9", "question")])
            await self.asker.cancel(self.ch)
            self.assertEqual(self.kinds(), [("9", "menu")])
        asyncio.run(go())


class Restart(Base):
    def test_the_file_is_built_anew_from_what_is_still_open(self):
        """Questions are kept on disk and are still open after a restart; menus are in memory, their buttons dead."""
        os.makedirs(self.state)
        with open(os.path.join(self.state, awaiting.FILE), "w") as f:
            json.dump([{"channel": "1", "kind": "menu", "since": 1.0}, {"channel": "2", "kind": "question", "since": 2.0}], f)
        ask.save("7-1", {"channel": "9", "questions": [Q1], "answers": {}, "messages": {}, "ts": 50.0, "host": None})
        ask.save("7-2", {"channel": "9", "kind": "plan", "questions": [Q1], "answers": {}, "messages": {}, "ts": 40.0})
        ask.save("7-3", {"channel": "9", "questions": [Q1], "answers": {}, "messages": {}, "ts": 30.0, "host": "lap", "owed": {}})
        with open(os.path.join(self.state, awaiting.FILE), "w") as f:
            json.dump({"at": 5.0, "waiting": [{"channel": "1", "kind": "menu", "since": 1.0}]}, f)   # the bot was killed with a menu shown
        awaiting.on = False   # (a new process)
        menus.Menus(None)
        with open(os.path.join(self.state, awaiting.FILE)) as f:
            self.assertEqual(json.load(f)["at"], 5.0, "nothing written before the bot says it runs")
        before = time.time()
        awaiting.start()
        self.assertGreaterEqual(self.whole()["at"], before, "and its time is the new writing's")
        self.assertEqual(self.file(), [{"channel": "9", "kind": "plan", "since": 40.0}, {"channel": "9", "kind": "question", "since": 50.0}])

    def test_nothing_open_is_an_empty_list(self):
        os.makedirs(self.state)
        with open(os.path.join(self.state, awaiting.FILE), "w") as f:
            f.write("left over, not even JSON")
        menus.Menus(None)
        awaiting.start()
        self.assertEqual(self.file(), [])

    def test_a_file_someone_removed_is_written_again(self):
        m = menus.Menus(None)
        awaiting.start()
        os.remove(os.path.join(self.state, awaiting.FILE))
        awaiting.note()
        self.assertEqual(self.file(), [])
        self.assertIsNotNone(m)


class NotTheRunningBot(Base):
    def test_nothing_is_written_until_the_bot_says_it_runs(self):
        """Tests and tools use these modules too: the running bot's file is not theirs to write."""
        awaiting.on = False
        m = menus.Menus(None)
        m.open["1"] = {"channel": 9, "msg": 1, "title": "t"}
        ask.save("7-1", {"channel": "9", "questions": [Q1], "answers": {}, "messages": {}, "ts": 50.0})
        ask.forget("7-1")
        self.assertFalse(os.path.exists(os.path.join(self.state, awaiting.FILE)))
        awaiting.start()
        self.assertEqual(self.kinds(), [("9", "menu")])

    def test_the_bot_says_so_as_soon_as_its_state_is_read_and_again_as_it_ends(self):
        """Manager on 2241b1c: not at on_ready (with Discord out of reach the file stayed the last process's, dead
        menus and all); as the bot ends in the ordinary way, the empty list."""
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, "bot", "main.py"), encoding="utf-8") as f:
            source = f.read()
        main = source[source.index("\ndef main():"):]
        self.assertEqual((source.count("awaiting.start()"), source.count("awaiting.stop()")), (1, 1))
        self.assertLess(main.index("bot = Bot()"), main.index("awaiting.start()"))
        self.assertLess(main.index("awaiting.start()"), main.index("bot.run("))
        self.assertLess(main.index("finally:"), main.index("awaiting.stop()"))
        self.assertLess(main.index("awaiting.stop()"), main.index("sys.exit(bot.exit_code)"))

    def test_an_ordinary_end_leaves_the_empty_list_and_nothing_is_written_after(self):
        m = menus.Menus(None)
        awaiting.start()
        m.open["1"] = {"channel": 9, "msg": 1, "title": "t"}
        ask.save("7-1", {"channel": "9", "questions": [Q1], "answers": {}, "messages": {}, "ts": 50.0})
        self.assertEqual(len(self.kinds()), 2)
        before = time.time()
        awaiting.stop()
        self.assertEqual(self.kinds(), [])
        self.assertGreaterEqual(self.whole()["at"], before)
        m.open.pop("1")
        ask.forget("7-1")
        ask.save("7-2", {"channel": "9", "questions": [Q1], "answers": {}, "messages": {}, "ts": 60.0})
        self.assertEqual(self.kinds(), [], "the bot is over: its file is not written any more")
        awaiting.stop()
        awaiting.start()
        self.assertEqual(self.kinds(), [("9", "question")], "the next bot lists the question that is still on disk")


class CannotWrite(Base):
    def test_the_menu_goes_on_and_it_is_said_when_it_begins_and_when_it_is_over(self):
        """A full disk, a folder that cannot be written: never in the way of the thing itself. One line when it
        begins, none for every menu and question while it lasts, one when the file is written again."""
        with open(self.state, "w") as f:   # where the folder should be, a file: nothing can be written under it
            f.write("x")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            m = menus.Menus(None)
            awaiting.start()
            for n in range(5):
                m.open[str(n)] = {"channel": 9, "msg": n, "title": "t"}
            self.assertEqual(len(m.open), 5)
            m.open.pop("1")
            lines = [line for line in out.getvalue().splitlines() if line]
            self.assertEqual(len(lines), 1, lines)
            self.assertTrue(lines[0].startswith("waiting: could not write state/waiting-on-owner.json"), lines)
            os.remove(self.state)   # the disk has room again
            m.open.pop("2")
            self.assertEqual(len(self.kinds()), 3, "written whole from what is held: nothing of the gap is missing")
            m.open.pop("3")
            lines = [line for line in out.getvalue().splitlines() if line]
            self.assertEqual(len(lines), 2, lines)
            self.assertEqual(lines[1], "waiting: state/waiting-on-owner.json is written again")
            os.chmod(self.state, 0o500)
            try:
                m.open.pop("4")
                m.open["9"] = {"channel": 9, "msg": 9, "title": "t"}
            finally:
                os.chmod(self.state, 0o700)
            lines = [line for line in out.getvalue().splitlines() if line]
            self.assertEqual(len(lines), 3 if os.geteuid() else 2, "and said again when it begins again")

    def test_a_question_is_asked_and_answered_all_the_same(self):
        async def go():
            real = os.replace

            def replace(a, b):
                if b.endswith(awaiting.FILE):
                    raise OSError(28, "No space left on device")
                return real(a, b)
            with mock.patch.object(os, "replace", replace), contextlib.redirect_stdout(io.StringIO()) as out:
                self.assertTrue(await self.asker.post(self.ch, {"ask_id": "7-1", "questions": [Q1]}))
                click = Click(self.ch, ask.custom_id("7-1", 0, "1"))
                await self.asker.on_interaction(click)
            self.assertIn("→ B", click.response.edited)
            self.assertIn("No space left on device", out.getvalue())
            self.assertEqual(len(out.getvalue().splitlines()), 1)
            with open(os.path.join(ask.ANSWERS, "7-1.json")) as f:
                self.assertEqual(json.load(f), {"answers": {"Which one?": "B"}})
        asyncio.run(go())

    def test_what_a_reader_gets_is_never_half_a_file(self):
        m = menus.Menus(None)
        awaiting.start()
        real = os.replace
        seen = []

        def replace(a, b):
            if b.endswith(awaiting.FILE):
                seen.append(self.file())   # the old one, whole, until the new one is renamed over it
            return real(a, b)
        with mock.patch.object(awaiting.os, "replace", replace):
            m.open["1"] = {"channel": 9, "msg": 1, "title": "t"}
        self.assertEqual(seen, [[]])
        self.assertEqual(self.kinds(), [("9", "menu")])


if __name__ == "__main__":
    unittest.main()
