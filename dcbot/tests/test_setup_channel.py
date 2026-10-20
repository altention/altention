"""C-48 / C-57 D3: #setup. A new installation whose setup is not done opens #setup (a Claude in the bot's tmux, in
~/.local/state/altention/setup/, permission auto, told to use the setup skill) and no #chat; ops/setup.py done asks
for #chat (open_chat), the reconcile for #setup again (open_setup), both through the spool, both doing nothing more
when the channel is there already; an update card's "ask Claude" opens #setup on an older installation; a start runs
setup.py reconcile --if-changed in a process of its own."""
import asyncio
import os
import tempfile
import unittest
from unittest import mock

from bot import config, posts
from tests import tmuxguard  # noqa: F401,E402  (no test reaches a real tmux server)


def run(coro):
    return asyncio.run(coro)


class Ch(object):
    def __init__(self, guild, name, cid):
        self.guild, self.name, self.id, self.category, self.sent = guild, name, cid, None, []

    async def send(self, text=None, **kw):
        self.sent.append(text)


class Guild(object):
    def __init__(self):
        self.text_channels, self.categories = [], []

    async def create_text_channel(self, name, topic=None, category=None):
        ch = Ch(self, name, 900 + len(self.text_channels))
        self.text_channels.append(ch)
        return ch

    async def create_category(self, name):
        cat = type("Cat", (), {"name": name, "position": len(self.categories), "text_channels": []})()
        self.categories.append(cat)
        return cat


def bot(state=None, done=False, home=None):
    from bot import main as botmain
    b = botmain.Bot.__new__(botmain.Bot)
    b.channels_state, b.audits, b.submitted, b.calls = dict(state or {}), [], [], []
    b.new_install, b.audit_ch = True, None
    b.guild = Guild()

    async def audit(text):
        b.audits.append(text)

    async def submit(message, text):
        b.submitted.append((message.channel.name, text))
    b.audit = audit
    b.tmux = type("T", (), {"submit": staticmethod(submit)})()
    b.save = lambda: None
    b.get_guild = lambda gid: b.guild
    b.get_channel = lambda cid: next((c for c in b.guild.text_channels if c.id == cid), None)
    return botmain, b


class Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = tmp.name
        self.env = mock.patch.dict(os.environ, {"HOME": self.home})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.folder = os.path.join(self.home, ".local", "state", "altention", "setup")
        os.makedirs(self.folder)

    def done(self, value):
        """The instance's setup.done as the engine reads it (missing: None)."""
        from bot import main as botmain

        class Alt(object):
            @staticmethod
            def setting(path, default=None):
                return value if path == "setup.done" else default

            @staticmethod
            def instance_dir():
                return os.path.join(self.home, "instance")
        return mock.patch.object(botmain.remote, "alt", lambda: Alt)


class OpenSetup(Base):
    def test_a_new_installation_whose_setup_is_not_done_gets_setup_and_no_chat(self):
        botmain, b = bot()
        with self.done(False):
            run(b.own_channels(b.guild))
        names = [c.name for c in b.guild.text_channels]
        self.assertIn(config.SETUP_CHANNEL, names)
        self.assertNotIn(config.CHAT_CHANNEL, names, "#chat waits for setup.py done")
        setup = next(c for c in b.guild.text_channels if c.name == config.SETUP_CHANNEL)
        st = b.channels_state[str(setup.id)]
        self.assertEqual((st["cwd"], st["permission"], st["mode"], st["configured"]), (self.folder, "auto", "tmux", True))
        self.assertIsNone(setup.category, "the main area")
        self.assertEqual(b.submitted, [(config.SETUP_CHANNEL, "請用 setup skill 帶使用者做完設定。")])

    def test_setup_done_or_no_such_key_is_chat_as_before(self):
        for value in (True, None):
            botmain, b = bot()
            with self.done(value), mock.patch.object(botmain.Bot, "chan", lambda self, ch: self.channels_state.setdefault(str(ch.id), {})):
                run(b.own_channels(b.guild))
            names = [c.name for c in b.guild.text_channels]
            self.assertIn(config.CHAT_CHANNEL, names, value)
            self.assertNotIn(config.SETUP_CHANNEL, names, value)

    def test_an_older_installation_not_done_and_without_setup_gets_it_again_and_one_with_it_is_left_alone(self):
        botmain, b = bot({"1": {"configured": True}})
        b.new_install = False
        with self.done(False):
            run(b.own_channels(b.guild))
            setup = next(c for c in b.guild.text_channels if c.name == config.SETUP_CHANNEL)
            self.assertEqual(len(b.submitted), 1)
            run(b.own_channels(b.guild))  # the next start: there already
        self.assertEqual(len(b.submitted), 1, "a start sends nothing again")
        self.assertEqual([c.name for c in b.guild.text_channels].count(config.SETUP_CHANNEL), 1)
        self.assertIs(next(c for c in b.guild.text_channels if c.name == config.SETUP_CHANNEL), setup)

    def test_no_folder_yet_nothing_made_and_said(self):
        os.rmdir(self.folder)
        botmain, b = bot()
        with self.done(False):
            self.assertIsNone(run(b.open_setup(b.guild)))
        self.assertEqual(b.guild.text_channels, [])
        self.assertIn("./install.sh", b.audits[-1])

    def test_a_channel_of_the_owners_by_that_name_is_left_alone(self):
        botmain, b = bot()
        theirs = run(b.guild.create_text_channel(config.SETUP_CHANNEL))
        self.assertIsNone(run(b.open_setup(b.guild)))
        self.assertNotIn(str(theirs.id), b.channels_state)
        self.assertEqual(b.submitted, [])
        self.assertIn("不是機器人開的設定頻道", b.audits[-1])

    def test_resend_and_opened_without_a_first_line(self):
        botmain, b = bot()
        ch = run(b.open_setup(b.guild, first=None))
        self.assertEqual(b.submitted, [], "only opened: the caller hands it its words")
        run(b.open_setup(b.guild, resend=False))
        self.assertEqual(b.submitted, [])
        self.assertIs(run(b.open_setup(b.guild)), ch)
        self.assertEqual(b.submitted, [(config.SETUP_CHANNEL, "請用 setup skill 帶使用者做完設定。")], "the reconcile's new questions")


class Spool(Base):
    def posts_of(self, b):
        b.posts = posts.Posts(b)
        return b.posts

    def test_open_chat_twice_makes_chat_once_and_says_once(self):
        """setup.py done run again sends open_chat again (manager 10-19): an existing #chat is left as it is."""
        botmain, b = bot()
        p = self.posts_of(b)

        async def ensure(guild, name, topic):
            return next((c for c in guild.text_channels if c.name == name), None) or await guild.create_text_channel(name)
        b.ensure_channel = ensure
        with self.done(True), mock.patch.object(botmain.Bot, "chan", lambda self, ch: self.channels_state.setdefault(str(ch.id), {})):
            for _ in range(2):
                run(p.handle({"channel": config.CHAT_CHANNEL, "open_chat": True, "text": ""}))
        chats = [c for c in b.guild.text_channels if c.name == config.CHAT_CHANNEL]
        self.assertEqual(len(chats), 1)
        self.assertEqual(len(chats[0].sent), 1, "its first words once")

    def test_open_setup_sends_the_first_line_again_and_makes_one_channel(self):
        botmain, b = bot()
        p = self.posts_of(b)
        for _ in range(2):
            run(p.handle({"channel": "setup", "open_setup": True, "text": ""}))
        self.assertEqual([c.name for c in b.guild.text_channels].count(config.SETUP_CHANNEL), 1)
        self.assertEqual(len(b.submitted), 2, "the first line each time the reconcile asks")

    def test_dcctl_lets_both_through_without_create(self):
        import importlib.machinery
        import importlib.util
        loader = importlib.machinery.SourceFileLoader("dcctl_d3", os.path.join(config.ROOT, "tools", "dcctl"))
        spec = importlib.util.spec_from_loader("dcctl_d3", loader)
        dcctl = importlib.util.module_from_spec(spec)
        loader.exec_module(dcctl)
        for key in ("open_chat", "open_setup"):
            self.assertIsNone(dcctl.post_target_error({"channel": "nowhere-yet", key: True}, None, []), key)


class AskClaude(Base):
    def test_an_older_installation_gets_setup_opened_for_the_card_and_the_text_handed_to_it(self):
        from bot import update_cards
        botmain, b = bot()
        b.chans = {}
        upd = run(b.guild.create_text_channel(config.UPDATE_CHANNEL))
        b.get_channel = lambda cid: next((c for c in b.guild.text_channels if c.id == cid), None)
        cards = update_cards.Cards(b)
        card = {"channel": upd.id, "message": 1, "buttons": [{"id": "ask", "label": "請 Claude 幫忙", "action": "ask_claude", "arg": "請讀 /x"}]}
        puts = []

        async def put(card_id, c, ch=None):
            puts.append(dict(c))
        cards.put = put
        with tempfile.TemporaryDirectory() as d, mock.patch.object(update_cards, "FOLDER", d):
            run(cards.do_ask_claude(None, "c", card, card["buttons"][0]))
        self.assertEqual(b.submitted, [(config.SETUP_CHANNEL, "請讀 /x")], "only the card's words, not the setup skill's")
        self.assertIn("已經交給", puts[-1]["notes"][-1])


class Reconcile(Base):
    def test_once_per_start_in_a_process_of_its_own_and_a_failure_said(self):
        from bot import main as botmain
        botmain_, b = bot()
        seen = []

        class Proc(object):
            returncode = 1

            async def communicate(self):
                return b"boom: the instance cannot be read\n", None

        async def spawn(*argv, **kw):
            seen.append(argv)
            return Proc()
        with mock.patch.object(botmain.asyncio, "create_subprocess_exec", spawn):
            run(b.reconcile())
        (argv,), = [seen]
        self.assertEqual(argv[1:], (os.path.join(os.path.dirname(config.ROOT), "ops", "setup.py"), "reconcile", "--if-changed"))
        self.assertIn("沒有做完", b.audits[-1])
        self.assertIn("boom", b.audits[-1])
        with mock.patch.object(botmain.os.path, "exists", lambda p: False), \
                mock.patch.object(botmain.asyncio, "create_subprocess_exec", spawn):
            run(b.reconcile())
        self.assertEqual(len(seen), 1, "no ops/setup.py: nothing run")
