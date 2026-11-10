"""C-121 in print mode (claude_bridge.py, `claude -p` per message): it had bypassPermissions written into its command
line. Now it takes the mode tmux_bridge.permission_of() gives (the channel's, else the installation's), and with
none nothing is run and the channel is told how to set one."""
import asyncio
import os
import types
import unittest
from unittest import mock

from bot import attachments, claude_bridge, main, tmux_bridge
from tests import fakepane  # noqa: F401  (the guard, and no real installation's settings)


class Ch(object):
    id = 42
    name = "c"

    def __init__(self):
        self.sent = []

    async def send(self, text, **kw):
        self.sent.append(text)

    def typing(self):
        return mock.MagicMock(__aenter__=mock.AsyncMock(), __aexit__=mock.AsyncMock(return_value=False))


class Turn(unittest.TestCase):
    def turn(self, st, settings):
        ch = Ch()
        host = types.SimpleNamespace(chan=lambda c: st, note=lambda c, s: "you are in #c", active=0, procs={},
                                     save=lambda: None)
        ran = mock.AsyncMock(return_value=claude_bridge.TurnResult("done", "s1"))
        with mock.patch.object(tmux_bridge, "installation", lambda name: settings.get(name)), \
                mock.patch.object(main, "run_turn", ran), \
                mock.patch.object(attachments, "DIR", fakepane.attachments_dir()):  # C-151: the channel's folder, made
            try:
                asyncio.run(main.Bot.turn(host, types.SimpleNamespace(channel=ch), "hello"))
            except Exception:  # what comes after the run (the reply's footer) needs more of the bot than this
                pass
        return ch.sent, ran

    def test_no_mode_runs_nothing_and_says_how(self):
        for settings in ({}, {"dcbot.default_permission": ""}):
            sent, ran = self.turn({"cwd": "/tmp"}, settings)
            ran.assert_not_called()
            self.assertEqual(sent, [tmux_bridge.UNSET])

    def test_the_installations_mode_and_the_channels_own_first(self):
        for st, settings, want in (({"cwd": "/tmp"}, {"dcbot.default_permission": "manual"}, "manual"),
                                   ({"cwd": "/tmp", "permission": "plan"}, {"dcbot.default_permission": "manual"}, "plan"),
                                   ({"cwd": "/tmp", "permission": "acceptEdits"}, {}, "acceptEdits")):
            _, ran = self.turn(st, settings)
            ran.assert_called_once()
            self.assertEqual(ran.call_args.args[4], want)

    def test_the_channels_attachments_folder_goes_along_c151(self):
        _, ran = self.turn({"cwd": "/tmp"}, {"dcbot.default_permission": "manual"})
        want = os.path.join(fakepane.attachments_dir(), str(Ch.id))
        self.assertEqual(ran.call_args.kwargs["dirs"], [want])
        self.assertTrue(os.path.isdir(want))
        os.rmdir(want)

    def test_the_command_line_carries_it(self):
        seen = []

        async def exec_(*cmd, **kw):
            seen.append(cmd)
            raise OSError("not run in a test")
        with mock.patch.object(asyncio, "create_subprocess_exec", exec_):
            with self.assertRaises(OSError):
                asyncio.run(claude_bridge.run_turn("hi", None, "/tmp", "note", "manual"))
        cmd = list(seen[0])
        self.assertEqual(cmd[cmd.index("--permission-mode") + 1], "manual")
        self.assertNotIn("bypassPermissions", cmd)


if __name__ == "__main__":
    unittest.main()
