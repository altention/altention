"""The pane object (3b step 2.4a): what the code that opens a conversation reads and presses through. It decides
nothing: each method is tmuxio's function of that name, looked up when called; the tests' own (fakepane.Pane) has
the same methods."""
import asyncio
import inspect
import unittest
from unittest import mock

from bot import tmuxio
from tests import fakepane

THERE = {"socket": "/tmp/x/default", "id": "%7", "host": "lap"}
HERE = {"socket": "/tmp/x/default", "id": "%7"}


class Recorder(object):
    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        async def call(*args):
            self.calls.append((name,) + args)
            return name
        return call


def public(cls):
    return sorted(n for n, v in vars(cls).items() if not n.startswith("_"))


class ThePane(unittest.TestCase):
    def test_each_method_is_the_function_of_that_name_with_the_target_in_front(self):
        for t in ("dc-x-1", HERE, THERE):
            fake = Recorder()
            with fakepane.use(fake):
                p = tmuxio.Pane(t)
                got = asyncio.run(self.all_of(p))
            self.assertEqual(got, ["alive", "raw_screen", "screen", "keys", "type_text", "cursor"])
            self.assertEqual(fake.calls, [("alive", t), ("raw_screen", t), ("screen", t, 15), ("keys", t, "Down", "Enter"),
                                          ("type_text", t, "hello"), ("cursor", t)])

    async def all_of(self, p):
        return [await p.alive(), await p.raw_screen(), await p.screen(15), await p.keys("Down", "Enter"),
                await p.type_text("hello"), await p.cursor()]

    def test_closing_ends_only_what_the_bot_started_there(self):
        for t, how in (("dc-x-1", "kill"), (HERE, "kill_pane"), (THERE, "kill_pane")):
            fake = Recorder()
            with fakepane.use(fake):
                asyncio.run(tmuxio.Pane(t).close())
            self.assertEqual(fake.calls, [(how, t)])

    def test_which_machine_and_how_the_owner_is_told_where(self):
        self.assertEqual([tmuxio.Pane(t).remote for t in ("dc-x-1", HERE, THERE)], [False, False, True])
        for t in ("dc-x-1", HERE, THERE):
            self.assertEqual(tmuxio.Pane(t).label, tmuxio.label(t))

    def test_a_pane_of_a_pane_is_itself_and_of_nothing_is_nothing(self):
        p = tmuxio.Pane("s")
        self.assertIs(tmuxio.pane(p), p)
        self.assertIsNone(tmuxio.pane(None))
        self.assertEqual(tmuxio.pane(HERE).t, HERE)

    def test_the_function_is_looked_up_when_called(self):
        p = tmuxio.Pane("s")  # made before the replacement
        with mock.patch.object(tmuxio, "alive", mock.AsyncMock(return_value="later")):
            self.assertEqual(asyncio.run(p.alive()), "later")


class TheTestsOwn(unittest.TestCase):
    def test_it_has_the_same_methods_taking_the_same(self):
        self.assertEqual(public(fakepane.Pane), public(tmuxio.Pane))
        for name in public(tmuxio.Pane):
            real, fake = getattr(tmuxio.Pane, name), getattr(fakepane.Pane, name)
            if isinstance(real, property):
                self.assertIsInstance(fake, property, name)
            else:
                self.assertEqual(str(inspect.signature(real)), str(inspect.signature(fake)), name)

    def test_it_hands_each_call_to_the_fake(self):
        for t, how in (("s", "kill"), (THERE, "kill_pane")):
            fake = Recorder()
            p = fakepane.Pane(fake, t)

            async def go():
                await p.alive(), await p.raw_screen(), await p.screen(3), await p.keys("Enter"), await p.type_text("a"), await p.cursor(), await p.close()
            asyncio.run(go())
            self.assertEqual(fake.calls, [("alive", t), ("raw_screen", t), ("screen", t, 3), ("keys", t, "Enter"), ("type_text", t, "a"),
                                          ("cursor", t), (how, t)])
            self.assertEqual(p.remote, t is THERE)

    def test_wait_ready_takes_it(self):
        """Given the tests' own pane, the wait reads and presses through it (not through tmuxio)."""
        from bot import tmux_bridge
        from tests.test_open_session import TRUST

        class Dialog(Recorder):
            async def alive(self, t):
                self.calls.append(("alive", t))
                return True

            async def raw_screen(self, t):
                return TRUST % ("❯", " ")
        fake, rt = Dialog(), tmux_bridge.Runtime()
        bridge = tmux_bridge.TmuxBridge.__new__(tmux_bridge.TmuxBridge)
        self.assertIs(tmuxio.pane(fakepane.Pane(fake)).fake, fake)
        with mock.patch.object(tmux_bridge, "START_SECS", 0.2), fakepane.use():
            self.assertFalse(asyncio.run(bridge.wait_ready(fakepane.Pane(fake, "mine"), rt)))
        self.assertIn(("keys", "mine", "Enter"), fake.calls)
        self.assertTrue(all(c[1] == "mine" for c in fake.calls), fake.calls)


if __name__ == "__main__":
    unittest.main()
