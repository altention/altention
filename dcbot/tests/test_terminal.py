"""Terminal channels: the shell's rc, history, and a real shell driven through tmux."""
import asyncio
import os
import shutil
import subprocess
import tempfile
import time
import unittest

import discord

from bot import config, terminal, tmuxio
from tests import tmuxguard  # noqa: F401,E402  (no test reaches a real tmux server)


class Pure(unittest.TestCase):
    def test_history(self):
        st = {}
        for c in ("ls", "git status", "ls", "nvidia-smi"):
            terminal.remember(st, c)
        self.assertEqual(terminal.history(st), ["nvidia-smi", "ls", "git status"], "newest first, no repeats")
        self.assertEqual(terminal.history(st, "GIT"), ["git status"])
        self.assertEqual(st["cmd_seq"], 4)

    def test_body(self):
        self.assertEqual(terminal.body("a```b", " x"), "```\na'''b\n``` x")
        long = terminal.body("y" * 5000, "")
        self.assertLess(len(long), 2000)
        self.assertTrue(long.startswith("```\n…\n"))

    def test_rc_loads_the_owners_config_then_adds_the_hook(self):
        with tempfile.TemporaryDirectory() as tmp:
            argv, env = terminal.rc_files(tmp, os.path.join(tmp, "done"))
            if os.path.basename(terminal.shell()) == "zsh":
                self.assertEqual(env, {"ZDOTDIR": tmp})
                rc = open(os.path.join(tmp, ".zshrc")).read()
                self.assertIn('source "$ZDOTDIR/.zshrc"', rc)
                self.assertIn("precmd_functions=(_dcbot_done", rc)
                self.assertIn('source "$HOME/.zshenv"', open(os.path.join(tmp, ".zshenv")).read())
            else:
                self.assertIn("--rcfile", argv)


class Chan(object):
    id, name = 31, "term"

    def __init__(self):
        self.sent, self.files = [], []

    async def send(self, text, view=None, file=None):
        m = Msg(text, view)
        self.sent.append(m)
        if file is not None:
            self.files.append(file)
        return m


class Msg(object):
    def __init__(self, content, view):
        self.content, self.view = content, view

    async def edit(self, content=None, view=None):
        self.content, self.view = content, view


class Bot(object):
    def __init__(self):
        self.channels_state = {}

    def chan(self, ch):
        return self.channels_state.setdefault(str(ch.id), {"cwd": "/tmp"})

    def save(self):
        pass


class Click(object):
    type = discord.InteractionType.component

    def __init__(self, ch, cid):
        self.channel, self.data, self.user = ch, {"custom_id": cid}, type("U", (), {"id": 111})()

        class R(object):
            async def defer(self):
                pass
        self.response = R()


@unittest.skipUnless(shutil.which("tmux"), "tmux not installed")
class RealShell(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.socket = "/tmp/dcterm-test-%d.sock" % os.getpid()
        self.saved = (terminal.DIR, config.OWNER_ID)
        terminal.DIR, config.OWNER_ID = self.tmp.name, 111

    def tearDown(self):
        subprocess.run(["tmux", "-S", self.socket, "kill-server"], stderr=subprocess.DEVNULL)
        if os.path.exists(self.socket):
            os.remove(self.socket)
        terminal.DIR, config.OWNER_ID = self.saved
        self.tmp.cleanup()

    def test_commands_output_exit_codes_input_and_ctrl_c(self):
        async def go():
            bot, ch = Bot(), Chan()
            t = terminal.Terminal(bot)
            bot.chan(ch).update({"home": {"socket": self.socket, "session": "t", "window": "sh"}, "kind": "terminal"})

            async def finish(n, secs=20):
                end = time.time() + secs
                while time.time() < end and (len(ch.sent) < n or ch.id in t.running):
                    await asyncio.sleep(0.2)
                return ch.sent[n - 1].content

            await t.run(ch, "echo hello-$((40+2)); false")
            out = await finish(1)
            self.assertIn("hello-42", out)
            self.assertIn("結束碼 1", out)
            await t.run(ch, "read x; echo got-$x")
            await asyncio.sleep(1)
            await t.run(ch, "abc")  # input for the command that is waiting
            self.assertIn("got-abc", await finish(2))
            await t.run(ch, "sleep 30")
            await asyncio.sleep(1.5)
            seq = bot.chan(ch)["cmd_seq"]
            await t.on_interaction(Click(ch, "term:%d:ctrlc:%d" % (ch.id, seq)))
            self.assertIn("結束碼 130", await finish(3))
            await t.run(ch, "seq 1 3000")
            out = await finish(4)
            self.assertIn("3000", out)
            self.assertEqual(len(ch.files), 1, "a long output also comes as a file")
            self.assertEqual(terminal.history(bot.chan(ch))[:2], ["seq 1 3000", "sleep 30"])
        asyncio.run(go())


if __name__ == "__main__":
    unittest.main()
