"""tests/fakepane.py itself: a replacement that cannot take is said aloud, and a test with a fake pane in use that
reaches a real tmux or ssh fails (2026-10-11: what makes the moves of stage 3b loud instead of silent)."""
import asyncio
import contextlib
import io
import types
import unittest
from unittest import mock

from bot import tmuxio
from tests import fakepane


class Replace(unittest.TestCase):
    def test_a_name_it_does_not_know(self):
        with self.assertRaisesRegex(AssertionError, "not a name it knows"):
            fakepane.replace("raw_scren", None)
        with self.assertRaisesRegex(AssertionError, "not a name it knows"):
            with fakepane.use(raw_scren=None):
                pass

    def test_a_name_the_module_no_longer_has(self):
        elsewhere = types.ModuleType("bot.connector")
        with mock.patch.dict(fakepane.WHERE, {"raw_screen": elsewhere}):
            with self.assertRaisesRegex(AssertionError, "bot.connector has no 'raw_screen' any more"):
                fakepane.replace("raw_screen", None)

    def test_a_function_only_handed_on_from_elsewhere(self):
        """`from .connector import raw_screen` left in the old module: replacing it there reaches no caller."""
        async def raw_screen(t):
            return ""
        raw_screen.__module__ = "bot.connector"
        with mock.patch.object(tmuxio, "raw_screen", raw_screen):
            with self.assertRaisesRegex(AssertionError, "only handed on from bot.connector"):
                fakepane.replace("raw_screen", None)

    def test_what_takes_is_put_back(self):
        real = tmuxio.raw_screen

        async def fake(t):
            return "fake"
        with fakepane.use(raw_screen=fake):
            self.assertIs(tmuxio.raw_screen, fake)
        self.assertIs(tmuxio.raw_screen, real)
        with fakepane.replace("ESC_GAP", 0.1):
            self.assertEqual(fakepane.cc_typing.ESC_GAP, 0.1)

    def test_a_fake_objects_methods_are_taken_by_their_names(self):
        fake = fakepane.Screens([fakepane.box("❯ ")])
        with fakepane.use(fake):
            self.assertEqual(tmuxio.raw_screen, fake.raw_screen)
            self.assertEqual(tmuxio.keys.__wrapped__, fake.keys, "(noted on the way for tests/keylog.py)")
            self.assertEqual(asyncio.run(tmuxio.screen("s")), "❯\n  ? for shortcuts", "what is built on them goes through them")


class Guard(unittest.TestCase):
    def test_reaching_the_real_tmux_fails_the_test(self):
        """A replacement that did not take: the code goes on to tmuxio.tmux, which would start `tmux -L <the bot's>`."""
        async def go():
            with fakepane.use(keys=mock.AsyncMock()):
                await tmuxio.raw_screen("a session of the bot that is running")
        with self.assertRaisesRegex(fakepane.Reached, "started a real tmux"):
            asyncio.run(go())

    def test_it_is_not_an_exception_the_bridge_can_swallow(self):
        self.assertFalse(issubclass(fakepane.Reached, Exception))

        async def go():
            with fakepane.use():
                try:
                    await tmuxio.alive("s")
                except Exception:  # as the bridge does around most of what it calls
                    pass
        with self.assertRaises(fakepane.Reached):
            asyncio.run(go())

    def test_one_reached_inside_a_task_nobody_looked_at_is_said_at_the_end(self):
        async def go():
            with fakepane.use():
                task = asyncio.ensure_future(tmuxio.alive("s"))
                await asyncio.sleep(0.05)
                self.assertTrue(task.done())
                task._log_traceback = False  # nobody awaits it: that is the case
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaisesRegex(fakepane.Reached, "has-session"):
            asyncio.run(go())

    def test_ssh_too_and_nothing_else(self):
        async def go():
            with fakepane.use():
                proc = await asyncio.create_subprocess_exec("true")  # any other program runs
                self.assertEqual(await proc.wait(), 0)
                await asyncio.create_subprocess_exec("/usr/bin/ssh", "somewhere", "true")
        with self.assertRaisesRegex(fakepane.Reached, "real ssh"):
            asyncio.run(go())

    def test_standing_in_for_tmux_itself_still_guards_a_direct_ssh(self):
        async def tmux(*args, **kw):
            return 0, "screen\n"

        async def go():
            with fakepane.use(tmux=tmux):
                return await tmuxio.raw_screen("s")
        self.assertEqual(asyncio.run(go()), "screen\n")

        async def direct():
            with fakepane.use(tmux=tmux):
                await asyncio.create_subprocess_exec("ssh", "somewhere", "true")
        with self.assertRaisesRegex(fakepane.Reached, "real ssh"):
            asyncio.run(direct())

    def test_a_list_of_patchers_has_the_guard_too(self):
        """For the tests that start and stop patchers themselves (a setUp with addCleanup)."""
        patchers = fakepane.patchers(alive=mock.AsyncMock(return_value=True))

        async def go():
            for p in patchers:
                p.start()
            try:
                await tmuxio.raw_screen("a session of the bot that is running")
            finally:
                for p in reversed(patchers):
                    p.stop()
        with self.assertRaisesRegex(fakepane.Reached, "started a real tmux"):
            asyncio.run(go())

    def test_a_use_inside_a_use_puts_the_new_fake_over_the_old(self):
        async def first(t):
            return "first"

        async def second(t):
            return "second"

        async def go():
            with fakepane.use(raw_screen=first):
                with fakepane.use(raw_screen=second):
                    inner = await tmuxio.raw_screen("s")
                return inner, await tmuxio.raw_screen("s")
        self.assertEqual(asyncio.run(go()), ("second", "first"))


if __name__ == "__main__":
    unittest.main()
