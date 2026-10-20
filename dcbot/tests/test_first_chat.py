"""The bot's own channels: their names come from the instance's settings, and a new installation's first start makes
#chat, a conversation in the instance folder that needs no settings card. No channel for changing the bot is made."""
import asyncio
import json
import os
import tempfile
import unittest
from unittest import mock

from bot import config
from tests import tmuxguard  # noqa: F401,E402  (no test reaches a real tmux server)


class Names(unittest.TestCase):
    def name(self, said, env=None):
        with mock.patch("altention.config.setting", lambda path, default=None: said(path) if callable(said) else said), \
                mock.patch.object(config, "_env", env or {}):
            return config.channel_name("guide", "guide")

    def test_the_instance_says(self):
        self.assertEqual(self.name(lambda p: "bot-guide" if p == "dcbot.channels.guide" else None), "bot-guide")
        self.assertEqual(self.name(" #manual "), "manual")

    def test_else_the_env_file_else_the_default(self):
        self.assertEqual(self.name(None, {"GUIDE_CHANNEL": "old"}), "old")
        self.assertEqual(self.name(None), "guide")
        for bad in ("", "#", 5, ["x"], {"a": 1}):
            self.assertEqual(self.name(bad), "guide", bad)

    def test_settings_that_cannot_be_read_are_the_default(self):
        def broken(path, default=None):
            raise ValueError("bad json")
        with mock.patch("altention.config.setting", broken), mock.patch.object(config, "_env", {}):
            self.assertEqual(config.channel_name("chat", "chat"), "chat")

    def test_the_system_category_is_named_where_the_channels_are(self):
        """Manager 2026-10-19: dcbot.channels.system (default 系統), not the bot's env file, so the engine (ops/setup.py)
        reads the same name without opening the file that holds the token."""
        self.assertEqual(self.name_of("system", "系統", lambda p: "下面" if p == "dcbot.channels.system" else None), "下面")
        self.assertEqual(self.name_of("system", "系統", None), "系統")
        import inspect
        self.assertIn('channel_name("system", "系統")', inspect.getsource(config))

    def name_of(self, key, default, said):
        with mock.patch("altention.config.setting", lambda path, d=None: said(path) if callable(said) else said), \
                mock.patch.object(config, "_env", {}):
            return config.channel_name(key, default)

    def test_no_development_channel_is_named(self):
        self.assertFalse(hasattr(config, "DEV_CHANNEL"))


class Ch(object):
    def __init__(self, name, id=7):
        self.name, self.id, self.category, self.sent = name, id, None, []

    async def send(self, text, **k):
        self.sent.append(text)


class FirstStart(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = tmp.name
        self.inst = os.path.join(self.dir, "instance")
        os.mkdir(self.inst)

    def bot(self, state=None):
        from bot import main as botmain
        path = os.path.join(self.dir, "channels.json")
        if state is not None:
            with open(path, "w") as f:
                json.dump(state, f)
        with mock.patch.object(botmain, "CHANNELS", path), mock.patch.object(botmain.Bot, "save", lambda self: None):
            bot = botmain.Bot()
        return botmain, bot

    def start(self, botmain, bot, inst="."):
        made = []

        async def ensure(guild, name, topic):
            ch = Ch(name, id=len(made) + 1)
            made.append(ch)
            return ch

        class Alt(object):
            @staticmethod
            def instance_dir():
                return self.inst if inst == "." else inst
        with mock.patch.object(bot, "ensure_channel", ensure), mock.patch.object(bot, "save", lambda: None), \
                mock.patch.object(botmain.remote, "alt", lambda: Alt):
            asyncio.run(bot.first_chat(object()))
        return made

    def test_a_new_installation_gets_chat_set_up_in_the_instance_folder(self):
        botmain, bot = self.bot()
        self.assertTrue(bot.new_install)
        made = self.start(botmain, bot)
        self.assertEqual([c.name for c in made], [config.CHAT_CHANNEL])
        st = bot.channels_state[str(made[0].id)]
        self.assertEqual((st["cwd"], st["configured"], st["mode"]), (self.inst, True, "tmux"))
        from bot import setup
        self.assertTrue(setup.configured(st), "no settings card")
        self.assertEqual(len(made[0].sent), 1)
        self.assertIn("#" + config.GUIDE_CHANNEL, made[0].sent[0])

    def test_without_an_instance_it_works_where_a_new_channel_would(self):
        botmain, bot = self.bot()
        made = self.start(botmain, bot, inst=None)
        self.assertEqual(bot.channels_state[str(made[0].id)]["cwd"], os.path.dirname(config.ROOT))

    def test_a_channel_already_set_up_is_left_as_it_is(self):
        botmain, bot = self.bot()
        bot.channels_state["1"] = {"cwd": "/w", "tmux": "dc-chat-1"}
        made = self.start(botmain, bot)
        self.assertEqual(bot.channels_state["1"], {"cwd": "/w", "tmux": "dc-chat-1"})
        self.assertEqual(made[0].sent, [])

    def test_an_installation_with_channels_is_not_new(self):
        self.assertFalse(self.bot({"1": {"cwd": "/w"}})[1].new_install)
        self.assertFalse(self.bot({})[1].new_install, "a state file, even empty, is not a first start")


if __name__ == "__main__":
    unittest.main()
