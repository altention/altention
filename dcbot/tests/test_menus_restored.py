"""C-189 (manager 11-12): a dialog still open on the screen when the bot starts again (its menus live in memory) is
taken in again: the card the channel's state names gets new buttons (no new card, no new ring), so --waiting lists it
and another channel's Claude may answer it; answered meanwhile, the old card and mention are closed. A Claude may
press named keys on a menu as the owner's /key could. Real screens: the 2.1.296 samples."""
import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))))
from altention import testing as _testing  # noqa: E402
_testing.guard_home()  # tests never touch the real home (altention/testing.py)
import types
import unittest

from bot import agentanswer, messages
from tests import test_menus
from tests import tmuxguard  # noqa: F401  (no test reaches a real tmux server)
from altention.tests.connectors.claude_code import samples  # noqa: E402  (after bot: test_connector_rules)


class Restored(unittest.TestCase):
    setUp, screens, post = test_menus.Pressed.setUp, test_menus.Pressed.screens, test_menus.Pressed.post

    def again(self, view):
        self.views = [view]
        with self.screens(), self.mock.patch.object(self.asyncio, "sleep", self.mock.AsyncMock()):
            return self.asyncio.run(self.menus.look_again(self.ch, self.bridge.host.chan(self.ch)))

    def counted(self):
        sent = []
        real = self.ch.send

        async def send(content, view=None, **kw):
            sent.append(content)
            return await real(content, view=view, **kw)
        self.ch.send = send
        return sent

    def test_the_old_card_gets_buttons_again(self):
        self.post()
        st = self.bridge.host.chan(self.ch)
        self.menus.open.clear()  # (a restart: nothing in memory, the state still says a dialog held it up)
        sent, calls = self.counted(), list(st.get("calls") or {})
        self.assertTrue(self.again(self.first))
        (token, m), = self.menus.open.items()
        self.assertEqual((m["msg"], m["call"], m["title"]), (5, "menu:5", messages.menus("restored_title")),
                         "the old card, its mention; a title that holds whatever dialog stands there now")
        content, view = self.edits[-1]
        self.assertIn(messages.menus("restored"), content)
        self.assertEqual([b.custom_id for b in view.children][-3:], ["key:%s:text" % token, "key:%s:enter" % token,
                                                                     "key:%s:esc" % token])
        self.assertEqual(sent, [], "no new card")
        self.assertEqual(list(st.get("calls") or {}), calls, "no new ring")
        self.assertIn("[m-%s] 終端機選單" % token, agentanswer.waiting_said(self.bridge, self.ch))
        n = len(self.edits)
        self.assertTrue(self.again(self.first), "one open already: left as it is")
        self.assertEqual(len(self.edits), n)

    def test_answered_meanwhile_the_old_card_is_closed(self):
        self.post()
        st = self.bridge.host.chan(self.ch)
        self.menus.open.clear()
        done = self.mock.AsyncMock()
        with self.mock.patch.object(self.bridge, "call_done", done):
            self.assertFalse(self.again(samples.screen("idle-fresh")))
        done.assert_awaited_once_with(self.ch, "menu:5")  # (its mention gets its ✅: #code-review-2 on d5bfa13c)
        self.assertNotIn("blocked", st)
        self.assertEqual(self.edits[-1], (None, None), "its buttons taken off")
        self.assertEqual(self.menus.open, {})

    def test_a_notification_meanwhile_leaves_one_menu(self):
        """Reviews on d5bfa13c: the restored menu is open before the card is edited, so a Notification arriving while
        it is (post() resolves what is open) leaves one menu for the channel, never two."""
        self.post()
        self.menus.open.clear()
        test = self

        class Slow(object):
            id = 5

            async def edit(self, content=None, view=None, **kw):
                test.assertEqual(len(test.menus.open), 1, "open before the await")
                test.edits.append((content, view))
        self.ch.get_partial_message = lambda mid: Slow()
        self.assertTrue(self.again(self.first))
        self.assertEqual(len(self.menus.open), 1)

    def test_its_card_taken_away_meanwhile_one_posted_anew(self):
        """C-205: the card the state names was deleted (altention dc post --delete, or by hand): not edited again on
        every round, its record let go and a new card posted with buttons."""
        import discord
        import types
        from bot import blocked
        self.post()
        st = self.bridge.host.chan(self.ch)
        self.menus.open.clear()

        class Gone(object):
            id = 5

            async def edit(self, **kw):
                raise discord.NotFound(types.SimpleNamespace(status=404, reason="x"), {"code": 10008, "message": "Unknown Message"})
        self.ch.get_partial_message = lambda mid: Gone()
        sent, done = self.counted(), self.mock.AsyncMock()
        with self.mock.patch.object(self.bridge, "call_done", done):
            self.assertTrue(self.again(self.first))
        done.assert_awaited_once_with(self.ch, "menu:5")
        self.assertEqual(len(sent), 1)
        self.assertIn(messages.menus("restored_title"), sent[0])
        (token, m), = self.menus.open.items()
        self.assertNotIn(messages.menus("restored"), m.get("excerpt") or "", "posted, not the edit of the old one")
        self.assertEqual((m["call"], blocked.of(st, "dialog")["msg"]), ("menu:%s" % m["msg"], m["msg"]), "(this fake gives every card id 5)")

    def test_an_open_menus_card_deleted_meanwhile_one_posted_anew(self):
        """C-205 (#code-review's must-fix on a830dc83): the card of a menu open while the bot runs is taken away
        (altention dc post --delete): its record let go, its mention closed, and the dialog still on the screen gets a
        new card at once, not on the next restart."""
        from bot import blocked
        self.post()
        st = self.bridge.host.chan(self.ch)
        (token, m), = self.menus.open.items()
        title = m["title"]
        sent, done = self.counted(), self.mock.AsyncMock()

        async def go():
            with self.screens(), self.mock.patch.object(self.asyncio, "sleep", self.mock.AsyncMock()), \
                    self.mock.patch.object(self.bridge, "call_done", done):
                self.views = [self.first]
                self.assertIsNone(await self.menus.card_deleted(self.ch, 6), "not its card: nothing")
                self.assertEqual(await self.menus.card_deleted(self.ch, 5), title)
                self.assertNotIn(token, self.menus.open)
                await self.menus.again
        self.asyncio.run(go())
        done.assert_any_await(self.ch, "menu:5")
        self.assertEqual(len(sent), 1, "posted anew")
        self.assertIn(title, sent[0], "its own title: no restart happened (rig drill 11-20)")
        self.assertNotIn(messages.menus("restored_title"), sent[0])
        (t2, m2), = self.menus.open.items()
        self.assertIsNot(m2, m, "a new record (its token, from the clock, may be the same ms)")
        self.assertEqual(blocked.of(st, "dialog")["msg"], m2["msg"])

    def test_no_card_known_posts_one(self):
        sent = self.counted()
        self.assertTrue(self.again(self.first))
        self.assertIn(messages.menus("restored_title"), sent[0], "a card of it (its ring after it)")
        self.assertEqual(len(self.menus.open), 1)

    def test_nothing_on_the_screen_nothing_done(self):
        sent = self.counted()
        self.assertFalse(self.again(samples.screen("idle-fresh")))
        self.assertFalse(self.again(samples.screen("model-panel")), "a panel is not a menu of options")
        self.assertEqual((sent, self.menus.open), ([], {}))

    def test_posts_waiting_take_the_dialog_in(self):
        """Unsent.hand_again(): the screen not the plain box because of a dialog nobody knew of: taken in."""
        st = self.bridge.host.chan(self.ch)
        st.setdefault("unsent", {})["t1"] = {"text": "hi", "at": 10 ** 10, "auto": True}
        self.views = [self.first]
        with self.screens(), self.mock.patch.object(self.asyncio, "sleep", self.mock.AsyncMock()):
            self.asyncio.run(self.bridge.unsent.hand_again(self.ch, st))
        self.assertEqual(len(self.menus.open), 1)
        self.assertIn("t1", st["unsent"], "still waiting for the dialog")


class RestoredStaysSafe(unittest.TestCase):
    """manager 11-16, for both reviews: a countdown menu taken in again is the owner's as ever, and a menu taken in
    again whose screen changed meanwhile is never pressed."""
    setUp, screens, post = test_menus.Pressed.setUp, test_menus.Pressed.screens, test_menus.Pressed.post
    again = Restored.again

    def answer(self, token, views, **post):
        return NamedKeys.answer(self, token, views, **post)

    def test_a_countdown_menu_taken_in_again_is_the_owners(self):
        for version, name in (("2.1.302", "countdown-colour-120x50-a"), ("2.1.322", "countdown-rig")):
            with self.subTest(version):
                view = samples.extra("P", name, version=version)
                self.menus.open.clear()
                self.pressed.clear()
                self.first = view
                self.post()
                self.menus.open.clear()  # (a restart)
                self.assertTrue(self.again(view))
                (token, _), = self.menus.open.items()
                for key in ("1", "enter", "esc", "down"):
                    ok, said = self.answer(token, [view], key=key, why="w")
                    self.assertEqual((ok, said), (False, "沒送：" + agentanswer.COUNTDOWN), key)
                self.assertEqual(self.pressed, [])

    def test_a_menu_taken_in_again_then_changed_is_not_pressed(self):
        self.post()
        self.menus.open.clear()
        self.assertTrue(self.again(self.first))
        (token, _), = self.menus.open.items()
        other = samples.screen("trust-dialog")
        ok, said = self.answer(token, [other, other], key="down", why="w")
        self.assertFalse(ok)
        self.assertIn("畫面已經變了", said)
        ok, said = self.answer(token, [other, other], key="1", why="w")
        self.assertEqual((ok, said), (False, "沒送：" + agentanswer.GONE_MENU), "its buttons went with the menu")
        self.assertEqual(self.pressed, [])


class NamedKeys(unittest.TestCase):
    setUp, screens, post = test_menus.Pressed.setUp, test_menus.Pressed.screens, test_menus.Pressed.post

    def answer(self, token, views, **post):
        self.views = list(views)
        sender = types.SimpleNamespace(id=7, name="lead")
        with self.screens(), self.mock.patch.object(self.asyncio, "sleep", self.mock.AsyncMock()):
            return self.asyncio.run(agentanswer.answer(self.bridge, self.ch, sender, dict(post, answer="m-" + token)))

    def test_a_claude_presses_named_keys(self):
        token = self.post()
        ok, said = self.answer(token, [self.first, self.first], key="Down", why="owner said the second")
        self.assertTrue(ok, said)
        self.assertEqual(self.pressed, ["Down"])
        self.assertEqual(self.bridge.host.audits[-1], "#c：#lead 的 Claude（自稱） 在選單按了 Down（為什麼：owner said the second）")
        ok, said = self.answer(token, [self.first], key="f5", why="w")
        self.assertFalse(ok)
        self.assertIn("--key enter／esc／up／down／left／right／tab／space", said)
        self.assertEqual(self.pressed, ["Down"])


if __name__ == "__main__":
    unittest.main()
