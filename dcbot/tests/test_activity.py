"""C-54a: the 打開 button of a plugin's Activity page (bot/activity.py): who may open what, what a press writes for
the plugin, and what a forged or stale button does."""
import asyncio
import importlib.machinery
import importlib.util
import json
import os
import re
import stat
import tempfile
import time
import unittest
from unittest import mock

import discord

from bot import activity, config, posts
from tests import tmuxguard  # noqa: F401,E402  (no test reaches a real tmux server)
from tests.test_posts import Bot, Chan

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OWNER, OTHER, GUILD = 42, 7, 500
PAGES = {("demo", "files"): {"title": "看檔案", "access": "owner"}, ("quiz", "play"): {"title": "測驗", "access": "guild"},
         ("rigact", "files"): {"title": "看檔案", "access": "owner"}}  # (rigact: a plugin name of the tests' own)


def rows(**decls):
    """plugin.on_this_machine()'s rows: {plugin: activity block}, all on, plus one off plugin with a page."""
    out = [(name, "/p/" + name, {"name": name, "activity": a}, "on", "") for name, a in decls.items()]
    return out + [("off", "/p/off", {"name": "off", "activity": {"pages": [{"id": "x", "access": "guild"}]}}, "off", "")]


class Seen(Chan):
    """A channel that also notes which message got a view."""
    async def send(self, text=None, view=None, **kw):
        self.views = getattr(self, "views", []) + [view]
        return await Chan.send(self, text, **kw)


class Response(object):
    def __init__(self, refuse=None):
        self.said, self.launched, self.refuse = [], 0, refuse

    async def send_message(self, text, ephemeral=False):
        self.said.append((text, ephemeral))
        self.at = time.time()

    async def launch_activity(self):
        if self.refuse:
            raise self.refuse
        self.launched += 1


def press(custom_id, user=OWNER, channel=1, guild=GUILD, refuse=None):
    return type("I", (), {"type": discord.InteractionType.component, "data": {"custom_id": custom_id},
                          "user": type("U", (), {"id": user})(), "channel": type("C", (), {"id": channel})(),
                          "guild_id": guild, "response": Response(refuse)})()


class Base(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.TemporaryDirectory(prefix="activity home ")
        self.addCleanup(self.home.cleanup)
        for p in (mock.patch.dict(os.environ, {"HOME": self.home.name}), mock.patch.object(config, "OWNER_ID", OWNER),
                  mock.patch.object(config, "GUILD_ID", GUILD)):
            p.start()
            self.addCleanup(p.stop)
        self.declared = dict(PAGES)
        p = mock.patch.object(activity, "pages", lambda rows=None: dict(self.declared))
        p.start()
        self.addCleanup(p.stop)
        self.bot = Bot()
        self.ch = Seen(1, "st-1")
        self.bot.chans[1] = self.ch
        self.bot.guild.text_channels = [self.ch]
        self.bot.channels_state = {"1": {"tmux": "dc-x"}}
        self.bot.launcher = activity.Activity(self.bot)
        p = mock.patch.dict(activity.embedded, {"on": True})  # (each test says what is known, when it matters)
        p.start()
        self.addCleanup(p.stop)

    def button(self, page="demo/files", **args):
        why, _ = asyncio.run(self.bot.launcher.post(self.ch, "報告", {"activity": page, "args": args}))
        self.assertIsNone(why)
        return next(reversed(self.bot.channels_state["1"]["activity"]))

    def intents(self, plugin="demo"):
        try:
            with open(activity.intents_path(plugin)) as f:
                return json.load(f)["intents"]
        except FileNotFoundError:
            return []

    def go(self, interaction):
        self.assertTrue(asyncio.run(self.bot.launcher.on_interaction(interaction)))
        return interaction.response


class Declared(unittest.TestCase):
    def test_pages_of_plugins_that_are_on(self):
        with mock.patch.dict(os.environ, {"HOME": tempfile.gettempdir()}):
            got = activity.pages(rows(demo={"pages": [{"id": "files", "title": "看檔案"}, {"id": "quiz", "access": "guild"}]},
                                      plain={"pages": []}))
        self.assertEqual(got, {("demo", "files"): {"title": "看檔案", "access": "owner"},
                               ("demo", "quiz"): {"title": "quiz", "access": "guild"}}, "owner by default; off plugins' pages left out")

    def test_a_value_that_got_past_the_check_opens_for_nobody_but_the_rules(self):
        got = activity.pages(rows(a={"pages": [{"id": "x", "access": "everyone"}]}))
        self.assertIsNone(got[("a", "x")]["access"])


class Buttons(Base):
    def test_the_button_holds_only_a_random_token(self):
        token = self.button(path="~/r.pdf")
        self.assertRegex(token, r"^[0-9a-f]{32}$")
        kept = self.bot.channels_state["1"]["activity"][token]
        self.assertEqual((kept["plugin"], kept["page"], kept["args"]), ("demo", "files", {"path": "~/r.pdf"}))
        self.assertEqual(self.ch.sent, ["報告"])
        with mock.patch.object(activity.secrets, "token_hex", lambda n: "f" * (2 * n)) as made:
            self.assertEqual(activity.remember({}, "a", "b", {}), "f" * 32)
        self.assertTrue(made)

    def test_old_buttons_are_dropped(self):
        st = {}
        first = activity.remember(st, "a", "b", {}, now=1000)
        for i in range(activity.KEEP):
            activity.remember(st, "a", "b", {}, now=2000 + i)
        self.assertNotIn(first, st["activity"])
        self.assertEqual(len(st["activity"]), activity.KEEP)
        activity.remember(st, "a", "b", {}, now=2000 + activity.KEEP_DAYS * 86400 + 100)
        self.assertEqual(len(st["activity"]), 1, "a week old: gone")

    def test_a_hand_edited_time_does_not_stop_new_buttons(self):
        """#code-review-2 on 0363369 (low): an `at` that is text (a hand-edited channels.json) made remember() raise,
        and no new button could be posted in that channel."""
        st = {"activity": {"a" * 32: {"plugin": "a", "page": "b", "at": "yesterday"}, "b" * 32: {"plugin": "a", "page": "b"},
                           "c" * 32: "not a button"}}
        token = activity.remember(st, "a", "b", {}, now=1000)
        self.assertEqual(list(st["activity"]), [token], "dropped, as pressed() takes them: gone")

    def test_a_page_not_declared_is_not_posted(self):
        for page in ("demo/none", "off/x", "Bad/x", "nope"):
            why, _ = asyncio.run(self.bot.launcher.post(self.ch, "x", {"activity": page}))
            self.assertTrue(why, page)
        self.assertEqual(self.ch.sent, [])


class Pressed(Base):
    def test_the_owner_opens_an_owner_page_and_the_plugin_learns_what(self):
        token = self.button(path="~/r.pdf")
        r = self.go(press("act:" + token, channel=1))
        self.assertEqual((r.launched, r.said), (1, []))
        got = self.intents()
        self.assertEqual(len(got), 1)
        self.assertEqual({k: got[0][k] for k in ("user", "channel", "guild", "page", "args", "access")},
                         {"user": str(OWNER), "channel": "1", "guild": str(GUILD), "page": "files", "args": {"path": "~/r.pdf"}, "access": "owner"})
        mode = stat.S_IMODE(os.stat(activity.intents_path("demo")).st_mode)
        self.assertEqual(mode, 0o600)
        self.assertEqual(stat.S_IMODE(os.stat(os.path.dirname(activity.intents_path("demo"))).st_mode) & 0o077, 0)
        self.assertTrue(activity.intents_path("demo").startswith(self.home.name), "from HOME at call time")
        self.assertEqual(self.intents("quiz"), [], "each plugin's own file: the others see nothing of it")

    def test_anyone_else_is_refused_an_owner_page_and_nothing_is_written(self):
        token = self.button()
        r = self.go(press("act:" + token, user=OTHER))
        self.assertEqual((r.launched, r.said), (0, [("這一頁只有擁有者能打開。", True)]))
        self.assertEqual(self.intents(), [])

    def test_a_guild_page_opens_for_anyone_in_this_server_only(self):
        token = self.button("quiz/play")
        r = self.go(press("act:" + token, user=OTHER))
        self.assertEqual(r.launched, 1)
        self.assertEqual([(i["user"], i["access"]) for i in self.intents("quiz")], [(str(OTHER), "guild")])
        r = self.go(press("act:" + token, user=OTHER, guild=999))
        self.assertEqual((r.launched, len(r.said)), (0, 1), "not this server")

    def test_forged_and_stale_buttons_open_nothing(self):
        token = self.button()
        for cid in ("act:" + "0" * 32, "act:" + token.upper(), "act:" + token + "x", "act:../../x", "act:"):
            r = self.go(press(cid))
            self.assertEqual((r.launched, r.said), (0, [("這顆按鈕已經失效了，請 Claude 再貼一次。", True)]), cid)
        r = self.go(press("act:" + token, channel=2))
        self.assertEqual(r.launched, 0, "a token of another channel's state: not this channel's button")
        self.assertEqual(self.intents(), [])
        self.assertFalse(asyncio.run(self.bot.launcher.on_interaction(press("resend:1:x"))), "others' buttons are not ours")

    def test_the_access_now_decides(self):
        token = self.button("quiz/play")
        self.declared[("quiz", "play")] = {"title": "測驗", "access": "owner"}  # made owner-only since it was posted
        r = self.go(press("act:" + token, user=OTHER))
        self.assertEqual(r.launched, 0)
        owner_page = self.button()
        self.declared[("demo", "files")] = {"title": "看檔案", "access": "guild"}
        self.assertEqual(self.go(press("act:" + owner_page, user=OTHER)).launched, 1, "and the other way round")
        del self.declared[("demo", "files")]  # the plugin turned off, or the page taken away
        r = self.go(press("act:" + owner_page))
        self.assertEqual((r.launched, r.said[0][0][:8]), (0, "這一頁已經不在了"[:8]))
        self.declared[("demo", "files")] = {"title": "看檔案", "access": None}
        self.assertEqual(self.go(press("act:" + owner_page)).launched, 0, "an access no rule knows")

    def test_declarations_that_cannot_be_read_or_are_slow_open_nothing(self):
        token = self.button()

        def broken(rows=None):
            raise ValueError("plugins.json")
        with mock.patch.object(activity, "pages", broken):
            r = self.go(press("act:" + token))
        self.assertEqual((r.launched, len(r.said)), (0, 1))

        def slow(rows=None):
            time.sleep(0.5)
            return dict(PAGES)
        with mock.patch.object(activity, "pages", slow), mock.patch.object(activity, "READ_SECS", 0.1):
            started = time.time()
            r = self.go(press("act:" + token))
            self.assertLess(r.at - started, 0.4, "answered at READ_SECS, not when the read ends (asyncio.run waits for the thread)")
        self.assertEqual((r.launched, len(r.said)), (0, 1))
        self.assertEqual(self.intents(), [])

    def refused(self, code):
        return discord.HTTPException(type("R", (), {"status": 400, "reason": "Bad Request"})(),
                                     {"code": code, "message": "Cannot use this interaction callback if the application does "
                                      "not have the EMBEDDED flag" if code == activity.NOT_EMBEDDED else "something else"})

    def test_known_off_the_one_answer_is_the_note(self):
        """#code-review-2 on 48739c8, the real application: a refused LAUNCH_ACTIVITY ends the interaction (a second
        answer is 404), so the owner saw only Discord's own failure. Known off: the note is the first answer."""
        token = self.button("rigact/files")
        activity.embedded["on"] = False
        r = self.go(press("act:" + token))
        self.assertEqual((r.launched, r.said), (0, [(activity.NOT_SET_UP % "rigact", True)]))
        self.assertEqual(self.intents("rigact"), [], "nothing written")

    def test_known_on_or_not_known_it_launches(self):
        token = self.button()
        for on in (True, None):
            activity.embedded["on"] = on
            r = self.go(press("act:" + token))
            self.assertEqual((r.launched, r.said), (1, []), on)
        self.assertEqual(len(self.intents()), 2)

    def test_refused_all_the_same_nothing_more_is_sent_and_the_next_press_knows(self):
        token = self.button("rigact/files")
        activity.embedded["on"] = None
        r = self.go(press("act:" + token, refuse=self.refused(activity.NOT_EMBEDDED)))
        self.assertEqual((r.launched, r.said), (0, []), "no second answer: it would be 404")
        self.assertIs(activity.embedded["on"], False)
        r = self.go(press("act:" + token))
        self.assertEqual((r.launched, r.said), (0, [(activity.NOT_SET_UP % "rigact", True)]), "the next press says it")
        activity.embedded["on"] = True
        r = self.go(press("act:" + token, refuse=self.refused(40060)))
        self.assertEqual((r.said, activity.embedded["on"]), ([], True), "another refusal: logged, nothing else known")

    def test_the_flags_are_read_before_and_kept_when_a_read_fails(self):
        activity.embedded["on"] = None
        flags = lambda on: type("F", (), {"embedded": on})()  # noqa: E731
        reads = []

        class App(object):
            application_flags = flags(False)  # the gateway's READY

            async def application_info(self):
                reads.append(1)
                if len(reads) == 1:
                    return type("A", (), {"flags": flags(True)})()
                raise OSError("network")
        launcher = activity.Activity(App())

        async def go():
            task = asyncio.ensure_future(launcher.watch())
            seen = []
            for _ in range(3):
                await asyncio.sleep(0.03)
                seen.append(activity.embedded["on"])
            task.cancel()
            return seen
        with mock.patch.object(activity, "FLAGS_EVERY", 0.02):
            seen = asyncio.run(go())
        self.assertGreaterEqual(len(reads), 2)
        self.assertEqual(seen[-1], True, "read on (True), then a failed read keeps it")
        activity.embedded["on"] = None
        with mock.patch.object(activity, "FLAGS_EVERY", 100):
            async def once():
                reads.clear()
                reads.append(1)  # (the next read fails)
                task = asyncio.ensure_future(launcher.watch())
                await asyncio.sleep(0.02)
                task.cancel()
            asyncio.run(once())
        self.assertIs(activity.embedded["on"], False, "READY's flags first; the failed read after keeps them")

    def test_an_intent_that_cannot_be_written_opens_nothing(self):
        token = self.button()
        os.makedirs(os.path.join(self.home.name, ".local", "state", "altention"))
        with open(os.path.join(self.home.name, ".local", "state", "altention", "activity"), "w"):
            pass  # a file where the folder should be
        r = self.go(press("act:" + token))
        self.assertEqual((r.launched, len(r.said)), (0, 1))

    def test_a_button_older_than_a_week_opens_nothing(self):
        """#code-review on a1fec93 (low): it was dropped only when a newer button of the channel was posted."""
        token = self.button()
        kept = self.bot.channels_state["1"]["activity"][token]
        kept["at"] = time.time() - activity.KEEP_DAYS * 86400 - 60
        r = self.go(press("act:" + token))
        self.assertEqual((r.launched, r.said), (0, [("這顆按鈕已經失效了，請 Claude 再貼一次。", True)]))
        kept["at"] = time.time() - activity.KEEP_DAYS * 86400 + 60
        self.assertEqual(self.go(press("act:" + token)).launched, 1, "within the week")
        del kept["at"]
        self.assertEqual(self.go(press("act:" + token)).launched, 0, "a record with no time: not trusted")
        self.assertEqual(len(self.intents()), 1)

    def test_after_a_restart_the_button_still_works(self):
        token = self.button()
        again = activity.Activity(self.bot)  # a bot started again: the buttons are in the channel's state
        self.bot.launcher = again
        self.assertEqual(self.go(press("act:" + token)).launched, 1)

    def test_intents_kept_are_the_recent_ones(self):
        now = time.time()
        activity.write_intent("demo", {"user": "1", "at": now - activity.INTENTS_SECS - 5}, now=now)
        for i in range(activity.INTENTS_KEPT + 3):
            activity.write_intent("demo", {"user": str(i), "at": now}, now=now)
        got = self.intents()
        self.assertEqual([i["user"] for i in got], [str(i) for i in range(3, activity.INTENTS_KEPT + 3)])
        with open(activity.intents_path("demo"), "w") as f:
            f.write("not json")
        activity.write_intent("demo", {"user": "x", "at": now}, now=now)
        self.assertEqual([i["user"] for i in self.intents()], ["x"], "a broken file is started again")
        self.assertEqual([n for n in os.listdir(os.path.dirname(activity.intents_path("demo"))) if n.startswith(".")], [],
                         "no temporary file left")


class FromAPost(Base):
    def setUp(self):
        super().setUp()
        self.other = Chan(2, "st-0")
        self.bot.chans[2] = self.other
        self.bot.guild.text_channels.append(self.other)
        self.bot.channels_state["2"] = {"tmux": "dc-y"}
        self.p = posts.Posts(self.bot)

    def test_a_post_shows_the_button_and_is_not_handed_to_a_claude(self):
        asyncio.run(self.p.handle({"channel": "st-1", "text": "報告好了", "activity": "demo/files", "args": {"path": "r.pdf"},
                                   "from_channel": "2", "from_name": "x"}))
        self.assertEqual(len(self.ch.sent), 1)
        self.assertIn("報告好了", self.ch.sent[0])
        self.assertTrue(self.ch.how[0]["embed"], "from another channel: a card")
        self.assertEqual(self.bot.tmux.given, [])
        asyncio.run(self.p.handle({"channel": "st-1", "text": "自己的", "activity": "demo/files", "from_channel": "1", "from_name": "x"}))
        self.assertEqual((self.ch.sent[-1], self.ch.how[-1]["embed"]), ("自己的", False), "its own channel: plain")

    def test_a_long_text_is_posted_whole_with_the_button_on_the_last_message(self):
        """#code-review on a1fec93 (low): 9000 characters were cut to 2000, and to the first of three cards."""
        long = "\n".join("line %04d %s" % (i, "x" * 80) for i in range(100))  # about 9000 characters
        asyncio.run(self.p.handle({"channel": "st-1", "text": long, "activity": "demo/files", "from_channel": "1", "from_name": "x"}))
        self.assertGreater(len(self.ch.sent), 3)
        self.assertEqual("\n".join(self.ch.sent).count("line "), 100, "all of it")
        self.assertEqual([v is not None for v in self.ch.views], [False] * (len(self.ch.sent) - 1) + [True])
        self.ch.sent, self.ch.views = [], []
        asyncio.run(self.p.handle({"channel": "st-1", "text": long, "activity": "demo/files", "from_channel": "2", "from_name": "x"}))
        self.assertGreater(len(self.ch.sent), 1)
        self.assertTrue(all(h["embed"] for h in self.ch.how[-len(self.ch.sent):]), "cards, from another channel")
        self.assertEqual("\n".join(self.ch.sent).count("line "), 100)
        self.assertEqual([v is not None for v in self.ch.views], [False] * (len(self.ch.sent) - 1) + [True])

    def test_a_page_not_there_is_told_to_the_sender(self):
        async def go():
            await self.p.handle({"channel": "st-1", "text": "x", "activity": "demo/none", "from_channel": "2", "from_name": "x"})
            await asyncio.sleep(0.05)
        asyncio.run(go())
        self.assertEqual(self.ch.sent, [])
        self.assertTrue([a for a in self.bot.audits if "--activity demo/none" in a])
        self.assertTrue([t for c, t in self.bot.tmux.given if c == 2 and "沒有貼出" in t])


class TheBot(unittest.TestCase):
    def test_the_bot_answers_the_buttons(self):
        """discord.Client has an `activity` of its own (its presence): the buttons' handler is the bot's `launcher`, and
        its on_interaction hands an act: press to it."""
        import shutil
        from bot import main as botmain
        state = tempfile.mkdtemp(prefix="activity bot ")
        self.addCleanup(shutil.rmtree, state, ignore_errors=True)
        with mock.patch.object(config, "STATE", state):
            bot = botmain.Bot()
        self.assertIsInstance(bot.launcher, activity.Activity)
        seen = []

        async def pressed(interaction, token):
            seen.append(token)
        with mock.patch.object(bot.launcher, "pressed", pressed):
            asyncio.run(bot.on_interaction(press("act:" + "a" * 32)))
        self.assertEqual(seen, ["a" * 32])


class CommandLine(unittest.TestCase):
    def load(self):
        path = os.path.join(os.path.dirname(ROOT), "altention", "dc", "post.py")
        loader = importlib.machinery.SourceFileLoader("dcpost_act", path)
        mod = importlib.util.module_from_spec(importlib.util.spec_from_loader("dcpost_act", loader))
        loader.exec_module(mod)
        return mod

    def test_activity_and_args(self):
        mod = self.load()
        p = mod.build(["st-1", "--activity", "demo/files", "--arg", "path=~/a b.pdf", "--arg", "page=3", "報告"], "", {}, "h", "/x")
        self.assertEqual((p["activity"], p["args"], p["text"]), ("demo/files", {"path": "~/a b.pdf", "page": "3"}, "報告"))
        self.assertNotIn("demo", mod.build(["st-1", "hi"], "", {}, "h", "/x"))
        for argv in (["st-1", "--activity", "files"], ["st-1", "--activity", "A/b"], ["st-1", "--activity"],
                     ["st-1", "--arg", "a=b", "x"], ["st-1", "--activity", "a/b", "--arg", "novalue"],
                     ["st-1", "--activity", "a/b", "--arg", "k=" + "x" * 3000]):
            with self.assertRaises(SystemExit, msg=argv):
                mod.build(argv, "", {}, "h", "/x")


if __name__ == "__main__":
    unittest.main()
