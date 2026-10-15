"""Discord "/" menu: what gets registered, and that a command reaches Bot.command() only for the owner."""
import asyncio
import re
import unittest

import discord

from bot import config, slash
from tests import tmuxguard  # noqa: F401,E402  (no test reaches a real tmux server)


class FakeResponse(object):
    def __init__(self):
        self.sent, self.deferred = [], None

    async def send_message(self, text, ephemeral=False):
        self.sent.append((text, ephemeral))

    async def defer(self, ephemeral=False):
        self.deferred = ephemeral


class FakeFollowup(object):
    def __init__(self):
        self.sent = []

    async def send(self, text, ephemeral=False, view=None, wait=False, file=None):
        self.sent.append((text, ephemeral))


class FakeInteraction(object):
    def __init__(self, user_id):
        self.user = type("U", (), {"id": user_id})()
        self.channel = type("C", (), {"id": 5, "name": "c"})()
        self.response, self.followup = FakeResponse(), FakeFollowup()


class FakeTmux(object):
    def __init__(self):
        self.typed, self.btws = [], []

    async def btw_open(self, ch, question, out=None):
        self.btws.append(question)
        await out("thread link")

    async def submit(self, message, text, out=None):
        self.typed.append((message.channel.id, text))
        await out("screen")


class Slash(unittest.TestCase):
    def setUp(self):
        self.owner, config.OWNER_ID = config.OWNER_ID, 111
        self.bot = discord.Client(intents=discord.Intents.default())
        self.bot.calls, self.bot.tmux, self.bot.st = [], FakeTmux(), {"cwd": "/nowhere"}
        self.bot.chan = lambda ch: self.bot.st
        self.bot.mode = lambda st: st.get("mode", "tmux")

        async def command(ch, text, send):
            self.bot.calls.append(text)
            await send("answer")

        async def audit(text):
            pass
        self.bot.command, self.bot.audit = command, audit
        self.tree = slash.setup(self.bot)

    def tearDown(self):
        config.OWNER_ID = self.owner

    def run_command(self, name, user_id=111, **kw):
        it = FakeInteraction(user_id)
        asyncio.run(self.tree.get_command(name, guild=self.tree.guild).callback(it, **kw))
        return it

    def test_registered_set_is_valid_for_discord(self):
        cmds = self.tree.get_commands(guild=self.tree.guild)
        self.assertEqual(sorted(c.name for c in cmds), ["attach", "btw", "cc", "commands", "compact", "context", "cwd", "detach", "files", "key",
                                                       "mode", "recap", "rename", "reset", "resume", "run", "screen", "setup", "status", "stop", "talklimit", "usage"])
        for c in cmds:
            d = c.to_dict(self.tree)
            self.assertRegex(d["name"], r"^[a-z0-9_-]{1,32}$")
            for item in [d] + d.get("options", []):
                self.assertTrue(1 <= len(item["description"]) <= 100, item)
            for o in d.get("options", []):
                for ch in o.get("choices", []):
                    self.assertLessEqual(len(ch["name"]), 100)
        self.assertTrue(re.match(r"^[0-9a-f]{64}$", slash.digest(self.tree)))

    def test_builtin_reaches_command_and_answers_privately(self):
        it = self.run_command("status")
        self.assertEqual(self.bot.calls, ["!status"])
        self.assertEqual((it.response.deferred, it.followup.sent), (True, [("answer", True)]))
        it = self.run_command("reset")
        self.assertEqual((it.response.deferred, it.followup.sent), (False, [("answer", False)]))

    def test_arguments(self):
        self.run_command("key", keys="Down Enter")
        self.run_command("mode", mode=None)
        self.run_command("mode", mode=discord.app_commands.Choice(name="x", value="print"))
        self.run_command("resume", conversation=None)
        self.run_command("resume", conversation="8f09af65-c276")
        self.run_command("cwd", path="~/x")
        self.assertEqual(self.bot.calls, ["!key Down Enter", "!mode ", "!mode print", "!resume ",
                                          "!resume 8f09af65-c276", "!cwd ~/x"])

    def test_claude_code_commands_from_the_catalog(self):
        cat = {"all": ["deep-research", "model", "status"], "entries": [
            {"name": "deep-research", "run": "anthropic-skills:deep-research", "description": "Skill：研究"},
            {"name": "status", "run": "status", "description": "would shadow the bot's own"},
            {"name": "Bad Name", "run": "x", "description": "d"}]}
        slash.add_catalog(self.tree, cat)
        self.assertEqual(self.tree.dynamic, ["deep-research"])
        self.run_command("deep-research", args="  LLM\nagents 2026 ")
        self.run_command("deep-research", args=None)
        self.run_command("cc", command="/model", args="opus")
        self.assertEqual(self.bot.tmux.typed, [(5, "/anthropic-skills:deep-research LLM agents 2026"),
                                              (5, "/anthropic-skills:deep-research"), (5, "/model opus")])
        self.run_command("status")
        self.assertEqual(self.bot.calls, ["!status"], "the bot's own command keeps its name")
        slash.add_catalog(self.tree, {"entries": []})
        self.assertIsNone(self.tree.get_command("deep-research", guild=self.tree.guild))
        self.run_command("commands", refresh=True)
        self.assertEqual(self.bot.calls[-1], "!commands refresh")

    def test_btw_opens_a_thread_and_answers_privately_with_its_link(self):
        it = self.run_command("btw", question=" what\nnow ")
        self.assertEqual(self.bot.tmux.btws, ["what now"])
        self.assertEqual((it.response.deferred, it.followup.sent), (True, [("thread link", True)]))

    def test_strangers_are_refused(self):
        for name in ("status", "usage"):
            it = self.run_command(name, user_id=222)
            self.assertEqual((self.bot.calls, self.bot.tmux.typed, it.response.deferred), ([], [], None))
            self.assertTrue(it.response.sent[0][1])  # told so privately

    def test_claude_commands_are_typed_into_the_session(self):
        it = self.run_command("usage")
        self.run_command("compact", instructions=" keep\nthe plan ")
        self.run_command("compact", instructions=None)
        self.assertEqual(self.bot.tmux.typed, [(5, "/usage"), (5, "/compact keep the plan"), (5, "/compact")])
        self.assertEqual(it.followup.sent, [("screen", False)])
        self.bot.st["mode"] = "print"
        it = self.run_command("context")
        self.assertEqual(len(self.bot.tmux.typed), 3)
        self.assertIn("print 模式", it.response.sent[0][0])


if __name__ == "__main__":
    unittest.main()
