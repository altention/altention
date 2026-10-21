"""C-74: a message not handed over for now (a dialog open: a temporary code) gets a 「再送一次」 button; a post (its
sender is another Claude) is handed again by itself once the channel moves on and its screen shows the input box."""
import asyncio
import time
import unittest
from unittest import mock

import discord

import bot  # noqa: F401  (first: it puts the engine's folder on the path)
from altention.tests.connectors.claude_code import samples
from bot import config, messages, posts, tmux_bridge, tmuxio, unsent
from tests import fakepane
from tests.fakepane import Screens, box
from tests.test_safety import run
from tests.test_turn_end import Ch, Host

DIALOG = samples.screen("permission")


class Notice(unittest.TestCase):
    """submit() on a dialog: the notice has the button, the words are kept; a "/" menu's answer has none."""
    def setUp(self):
        tmuxio._esc_at.clear()
        self.ch = Ch()
        self.bridge = tmux_bridge.TmuxBridge(Host(self.ch))

    def send(self, message, text, out=None):
        async def nothing(*a, **k):
            pass
        return run(self.bridge.submit(message, text, out=out), Screens([DIALOG]), mock.patch.object(self.bridge, "ensure", nothing),
                   mock.patch.object(tmux_bridge, "SUBMIT_DELAY", 0), mock.patch.object(tmux_bridge, "RECEIPT_SECS", 0.01))

    def test_a_dialog_open_gets_a_button_and_the_words_are_kept(self):
        r = self.send(posts.Origin(self.ch), "hello")
        self.assertEqual(r.code, "claude_code.dialog")
        msg = self.ch.sent[-1]
        words = messages.not_sent("claude_code.dialog", **r.data)
        self.assertEqual(msg.content, words.replace("再重發一次", "處理好之後按下面的「再送一次」"), "manager on C-74")
        button, = msg.view.children
        self.assertEqual(button.label, "再送一次")
        token = button.custom_id.split(":")[2]
        self.assertEqual(button.custom_id, "resend:5:%s" % token)
        entry = self.bridge.host.chan(self.ch)["unsent"][token]
        self.assertEqual((entry["text"], entry["auto"], entry["msg"]), ("hello", False, msg.id))

    def test_a_post_is_kept_to_go_again_by_itself(self):
        self.send(posts.Origin(self.ch, auto=True, sender=1), "（這是 #st-1 傳到這個頻道的訊息）\nhi")
        entry, = self.bridge.host.chan(self.ch)["unsent"].values()
        self.assertEqual((entry["auto"], entry["from"]), (True, 1))
        self.assertNotIn("src", entry, "a post has no message of the owner's")
        self.assertTrue(self.ch.sent[-1].content.endswith("對話框關掉後會自己補送。"), self.ch.sent[-1].content)

    def test_the_owners_message_is_remembered_for_its_eyes(self):
        class Message(object):
            channel, id, content = self.ch, 4242, "hello"

            async def add_reaction(self, emoji):
                pass
        Message.channel = self.ch
        self.send(Message(), "hello")
        entry, = self.bridge.host.chan(self.ch)["unsent"].values()
        self.assertEqual(entry["src"], 4242)

    def test_the_slash_menus_answer_has_no_button(self):
        said = []

        async def out(text):
            said.append(text)
        self.send(posts.Origin(self.ch), "hello", out=out)
        self.assertEqual(len(said), 1)
        self.assertNotIn("unsent", self.bridge.host.chan(self.ch))

    def test_handed_again_quietly_nothing_is_said(self):
        st = self.bridge.host.chan(self.ch)
        r = self.send(unsent.Again(self.ch, {"text": "x", "auto": True}, quiet=True), "x")
        self.assertEqual((r.code, self.ch.sent, st.get("unsent")), ("claude_code.dialog", [], None))


class Worded(unittest.TestCase):
    def test_every_way_of_saying_send_it_again(self):
        for code, (_, lasting, words) in messages.NOT_SENT.items():
            if lasting != messages.TEMPORARY:
                continue
            for auto in (False, True):
                said = unsent.worded(words, code, auto)
                self.assertNotRegex(said, unsent.AGAIN.pattern, (code, auto))
                self.assertIn("會自己補送" if auto else "按下面的「再送一次」", said, (code, auto))
        self.assertEqual(unsent.worded("等那台連得上之後再發一次就好；不需要 `!detach`。", "machine_unreachable", False),
                         "等那台連得上之後按下面的「再送一次」；不需要 `!detach`。")
        self.assertEqual(unsent.worded("沒有可以換的句子。", "x", False), "沒有可以換的句子。\n-# 處理好之後按下面的「再送一次」")


class Eyes(unittest.TestCase):
    def test_sent_again_the_owners_message_gets_its_eyes_a_post_none(self):
        added = []

        class Partial(object):
            def __init__(self, mid):
                self.mid = mid

            async def add_reaction(self, emoji):
                added.append((self.mid, emoji))
        ch = Ch()
        ch.get_partial_message = Partial
        asyncio.run(unsent.Again(ch, {"text": "x", "src": 4242}).add_reaction("👀"))
        asyncio.run(unsent.Again(ch, {"text": "x", "auto": True}).add_reaction("👀"))
        self.assertEqual(added, [(4242, "👀")])


class Bridge(object):
    def __init__(self, ch, results=()):
        self.host, self.given, self.results = Host(ch), [], list(results)

    async def submit(self, message, text):
        self.given.append((type(message).__name__, text, getattr(message, "quiet", False)))
        return self.results.pop(0) if self.results else None


class Refused(object):
    code = "claude_code.dialog"

    def __bool__(self):
        return False


class Button(unittest.TestCase):
    def setUp(self):
        self.ch = Ch()
        self.edited, self.private = [], []
        self.saved = config.OWNER_ID
        config.OWNER_ID = 42

    def tearDown(self):
        config.OWNER_ID = self.saved

    def press(self, u, token, user=42):
        test = self

        class Resp(object):
            async def edit_message(self, view=None):
                test.edited.append(("view", view))

            async def send_message(self, text, ephemeral=False):
                test.private.append(text)

        class Followup(object):
            async def send(self, text, ephemeral=False):
                test.private.append(text)

        class Msg(object):
            content = "⚠️ 沒有送出"

            async def edit(self, content=None, view=None):
                test.edited.append(("content", content))

        who = type("U", (), {"id": user})()

        class Click(object):
            type = discord.InteractionType.component
            data = {"custom_id": "resend:5:%s" % token}
            channel, response, followup, message = self.ch, Resp(), Followup(), Msg()
        Click.user = who
        return asyncio.run(u.on_interaction(Click()))

    def test_pressed_it_goes_into_the_queue_again(self):
        b = Bridge(self.ch)
        u = unsent.Unsent(b)
        b.host.chan(self.ch)["unsent"] = {"t1": {"text": "hello", "at": time.time(), "msg": 1, "auto": False}}
        self.assertTrue(self.press(u, "t1"))
        self.assertEqual(b.given, [("Again", "hello", False)])
        self.assertEqual(self.edited, [("view", None), ("content", "⚠️ 沒有送出\n✅ 已再送一次")])
        self.assertEqual(b.host.chan(self.ch)["unsent"], {})
        self.edited.clear()
        self.press(u, "t1")
        self.assertEqual(self.private, [messages.note("resend.expired")], "pressed twice: once only")

    def test_held_up_again_says_so(self):
        b = Bridge(self.ch, [Refused()])
        u = unsent.Unsent(b)
        b.host.chan(self.ch)["unsent"] = {"t1": {"text": "hello", "at": time.time(), "msg": 1}}
        self.press(u, "t1")
        self.assertEqual(self.edited[-1], ("content", "⚠️ 沒有送出\n" + messages.note("resend.still")))

    def test_too_old_or_not_the_owner(self):
        b = Bridge(self.ch)
        u = unsent.Unsent(b)
        b.host.chan(self.ch)["unsent"] = {"t1": {"text": "hello", "at": time.time() - unsent.KEEP_SECS - 1}}
        self.press(u, "t1", user=7)
        self.assertEqual((self.private, b.given), (["只有擁有者能用。"], []))
        self.press(u, "t1")
        self.assertEqual((self.private[-1], b.given), (messages.note("resend.expired"), []))
        self.assertFalse(asyncio.run(u.on_interaction(type("I", (), {"type": discord.InteractionType.component,
                                                                       "data": {"custom_id": "retry:5"}})())))


class ByItself(unittest.TestCase):
    """Posts held up are handed again once the channel moves on and the box is back; the owner's never."""
    def setUp(self):
        self.ch = Ch()
        self.edits = []
        edits = self.edits

        class Partial(object):
            id = 1

            async def edit(self, view=None):
                edits.append(view)
        self.ch.get_partial_message = lambda mid: Partial()

        async def send(content, view=None, silent=False, **kw):
            self.ch.sent.append(content)
        self.ch.send = send

    def go(self, b, screens):
        u = unsent.Unsent(b)
        st = b.host.chan(self.ch)
        now = time.time()
        st["unsent"] = {"a": {"text": "one", "at": now - 3, "msg": 1, "auto": True, "from": 1},
                        "b": {"text": "two", "at": now - 2, "msg": None, "auto": True},
                        "c": {"text": "owner's", "at": now - 1, "msg": 2, "auto": False}}
        with fakepane.use(Screens(screens)):
            asyncio.run(u.hand_again(self.ch, st))
        return u, st

    def test_the_box_back_they_go_in_order_the_owners_stays(self):
        b = Bridge(self.ch)
        _, st = self.go(b, [box("❯ ")])
        self.assertEqual(b.given, [("Again", "one", True), ("Again", "two", True)])
        self.assertEqual(list(st["unsent"]), ["c"], "what the owner typed is never sent again by itself")
        self.assertEqual(self.ch.sent, [messages.note("resend.handed")], "said once, under its notice")
        self.assertEqual(self.edits, [None], "its button gone")

    def test_another_dialog_nothing_goes(self):
        b = Bridge(self.ch)
        _, st = self.go(b, [DIALOG])
        self.assertEqual(b.given, [])
        self.assertEqual(sorted(st["unsent"]), ["a", "b", "c"])

    def test_held_up_after_all_it_keeps_its_place(self):
        b = Bridge(self.ch, [Refused()])
        _, st = self.go(b, [box("❯ ")])
        self.assertEqual(b.given, [("Again", "one", True)], "the round ends at the first held up")
        self.assertEqual(sorted(st["unsent"]), ["a", "b", "c"])

    def test_one_too_old_is_given_up_where_it_came_from(self):
        b = Bridge(self.ch)
        u = unsent.Unsent(b)
        st = b.host.chan(self.ch)
        st["unsent"] = {"a": {"text": "one", "at": time.time() - unsent.KEEP_SECS - 5, "auto": True, "from": 5},
                        "c": {"text": "owner's", "at": time.time() - unsent.KEEP_SECS - 5, "auto": False}}
        asyncio.run(u.expire())
        self.assertEqual(st["unsent"], {})
        said, = self.ch.sent
        self.assertIn("一直沒能交給 #c 的 Claude（24 小時內", said)
        self.assertTrue(said.endswith("：one"))

    def test_moved_starts_a_round_only_with_posts_waiting(self):
        b = Bridge(self.ch)
        u = unsent.Unsent(b)
        with mock.patch.object(u, "hand_again", mock.Mock(return_value=asyncio.sleep(0))) as hand:
            u.moved(self.ch, {"unsent": {"c": {"text": "owner's", "auto": False}}})
            hand.assert_not_called()

            async def go():
                u.moved(self.ch, {"unsent": {"a": {"text": "x", "auto": True, "at": 1}}})
                await asyncio.sleep(0)
            asyncio.run(go())
            hand.assert_called_once()


class Races(unittest.TestCase):
    """#code-review on 5a9fe8d: a post's button pressed while a round hands posts again sent it twice; a failure on
    the way lost it; and nothing looked again at posts waiting when no event came."""
    def setUp(self):
        self.ch = Ch()
        self.saved = config.OWNER_ID
        config.OWNER_ID = 42

        class Partial(object):
            id = 1

            async def edit(self, view=None):
                pass
        self.ch.get_partial_message = lambda mid: Partial()
        sent = self.ch.sent

        async def send(content, view=None, silent=False, **kw):
            sent.append((content, view))
            return type("M", (), {"id": 900 + len(sent)})()
        self.ch.send = send

    def tearDown(self):
        config.OWNER_ID = self.saved

    def click(self, token):
        class Resp(object):
            async def edit_message(self, view=None):
                pass

        class Followup(object):
            async def send(self, text, ephemeral=False):
                pass

        class Msg(object):
            content = "⚠️"

            async def edit(self, content=None, view=None):
                pass
        who = type("U", (), {"id": 42})()

        class Click(object):
            type = discord.InteractionType.component
            data = {"custom_id": "resend:5:%s" % token}
            channel, response, followup, message = self.ch, Resp(), Followup(), Msg()
        Click.user = who
        return Click()

    def test_its_button_pressed_while_the_round_hands_posts_again_it_goes_in_once(self):
        ch = self.ch
        given = []

        class Slow(object):
            host = Host(ch)

            async def submit(self, message, text):
                given.append(text)
                await asyncio.sleep(0.5)  # waits for its receipt
        b = Slow()
        u = unsent.Unsent(b)
        st = b.host.chan(ch)
        now = time.time()
        st["unsent"] = {"p1": {"text": "P1", "at": now - 2, "msg": 1, "auto": True},
                        "p2": {"text": "P2", "at": now - 1, "msg": 2, "auto": True}}

        async def go():
            round_ = asyncio.ensure_future(u.hand_again(ch, st))
            await asyncio.sleep(0.1)
            await u.on_interaction(self.click("p2"))  # the dialog is gone: the owner presses P2's button
            await round_
        with fakepane.use(Screens([box("❯ ")])):
            asyncio.run(go())
        self.assertEqual(sorted(given), ["P1", "P2"], "P2 once, not twice")

    def test_a_failure_on_the_way_keeps_it_and_says_where_it_came_from(self):
        ch = self.ch

        class Broken(object):
            host = Host(ch)

            async def submit(self, message, text):
                raise OSError("tmux: no server running")
        b = Broken()
        u = unsent.Unsent(b)
        st = b.host.chan(ch)
        st["unsent"] = {"p1": {"text": "P1", "at": time.time(), "msg": 1, "auto": True, "from": 5}}
        with fakepane.use(Screens([box("❯ ")])), self.assertRaises(OSError):
            asyncio.run(u.hand_again(ch, st))
        self.assertIn("p1", st["unsent"], "back in its place")
        said, _ = ch.sent[-1]
        self.assertIn("補交給 #c 的 Claude 時出錯（OSError: tmux: no server running），這則還留著", said)
        st["unsent"] = {"o1": {"text": "mine", "at": time.time(), "msg": 1, "auto": False}}
        with self.assertRaises(OSError):
            asyncio.run(u.on_interaction(self.click("o1")))
        self.assertIn("o1", st["unsent"], "the owner's too")
        said, button = ch.sent[-1]
        self.assertIn("再送一次時出錯（OSError", said)
        self.assertEqual(button.children[0].custom_id, "resend:5:o1", "with a button again")
        self.assertEqual(st["unsent"]["o1"]["msg"], 900 + len(ch.sent))

    def test_the_same_error_again_and_again_is_said_once(self):
        """Manager on 0b4f5f4: said the first time, again only for another error; once through, said it went."""
        ch = self.ch
        errors = [OSError("tmux: no server running")] * 3 + [OSError("ssh: timed out"), None]

        class Flaky(object):
            host = Host(ch)

            async def submit(self, message, text):
                e = errors.pop(0)
                if e is not None:
                    raise e
        b = Flaky()
        u = unsent.Unsent(b)
        st = b.host.chan(ch)
        st["unsent"] = {"p1": {"text": "P1", "at": time.time(), "msg": 1, "auto": True, "from": 5}}
        with fakepane.use(Screens([box("❯ ")])):
            for _ in range(4):
                with self.assertRaises(OSError):
                    asyncio.run(u.hand_again(ch, st))
            asyncio.run(u.hand_again(ch, st))
        said = [t for t, _ in ch.sent if t.startswith(("⚠️ 補交", "✅ 剛才"))]
        self.assertEqual(len(said), 3, said)
        self.assertIn("tmux: no server running", said[0])
        self.assertIn("ssh: timed out", said[1], "another error: said")
        self.assertTrue(said[2].startswith("✅ 剛才補交給 #c 的 Claude 出錯的那則，已經交到了"))
        self.assertEqual(st["unsent"], {})

    def test_the_chores_and_the_start_look_again_at_posts_waiting(self):
        b = Bridge(self.ch)
        u = unsent.Unsent(b)
        b.host.channels_state = {"5": {"unsent": {"p": {"text": "x", "at": time.time(), "auto": True}}},
                                 "6": {"unsent": {"o": {"text": "y", "at": time.time(), "auto": False}}}}
        moved = []
        u.moved = lambda ch, st: moved.append(ch.id)
        asyncio.run(u.expire())
        self.assertEqual(moved, [5], "only where posts wait")


class Triggers(unittest.TestCase):
    def test_an_event_of_its_turn_and_a_menu_button_start_a_round(self):
        import os
        import tempfile
        from bot import ask
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        saved = (ask.ASKS, ask.ANSWERS, config.INTERIM)
        ask.ASKS, ask.ANSWERS, config.INTERIM = os.path.join(tmp.name, "asks"), os.path.join(tmp.name, "answers"), 0
        self.addCleanup(lambda: setattr(ask, "ASKS", saved[0]) or setattr(ask, "ANSWERS", saved[1]) or setattr(config, "INTERIM", saved[2]))
        ch = Ch()
        bridge = tmux_bridge.TmuxBridge(Host(ch))
        moved = []
        bridge.unsent.moved = lambda c, st: moved.append(c.id)
        asyncio.run(bridge.handle({"channel": "5", "hook_event_name": "UserPromptSubmit", "session_id": "s", "prompt": "go"}))
        self.assertEqual(moved, [5])
        st = bridge.host.chan(ch)
        token = "77"
        bridge.menus.open[token] = {"channel": 5, "msg": 1, "title": "t"}

        class Resp(object):
            async def defer(self):
                pass

        class Click(object):
            channel, response = ch, Resp()

        async def nothing(*a, **k):
            return None
        with mock.patch.object(bridge.menus, "press", nothing), fakepane.use(Screens([box("❯ ")]), alive=mock.AsyncMock(return_value=True)):
            asyncio.run(bridge.menus.act(Click(), token, "1"))
        self.assertEqual(moved, [5, 5], "a menu button pressed: the dialog may be gone")
        self.assertIsNotNone(st)


class PostsBehind(unittest.TestCase):
    def test_a_post_that_comes_while_others_wait_goes_behind_them(self):
        from tests.test_posts import Bot, Chan
        b = Bot()
        a, c = Chan(1, "st-1"), Chan(2, "st-0")
        b.chans, b.guild.text_channels = {1: a, 2: c}, [a, c]
        b.channels_state = {"1": {"tmux": "dc-a"}, "2": {"tmux": "dc-b", "unsent": {
            "a": {"text": "first", "at": time.time(), "auto": True}}}}
        b.tmux.unsent = unsent.Unsent(type("B", (), {"host": b})())
        moved = []
        b.tmux.unsent.moved = lambda ch, st: moved.append(ch.id)
        asyncio.run(posts.Posts(b).handle({"channel": "st-0", "text": "second", "from_channel": "1"}))
        self.assertEqual(b.tmux.given, [], "not typed ahead of the one waiting")
        waiting = [e["text"] for _, e in b.tmux.unsent.waiting_posts(b.channels_state["2"])]
        self.assertEqual(waiting[0], "first")
        self.assertTrue(waiting[1].endswith("second"))
        self.assertEqual(moved, [2])


if __name__ == "__main__":
    unittest.main()
