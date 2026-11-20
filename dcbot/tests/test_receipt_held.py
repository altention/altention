"""A post held by a pause (--pause-until) or refused behind a full talk-limit card, sent with altention dc post --wait:
its sender learns that from the receipt dcctl waits for (manager 11-20). From the real writer: dcctl post takes "wait"
for itself and writes "receipt" in its place, so the bot never saw "wait"; and the receipt was written as soon as the
post was on Discord (shown()), before the bot knew it would be held, so a replied() came too late either way."""
import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))))
from altention import testing as _testing  # noqa: E402
_testing.guard_home()  # tests never touch the real home (altention/testing.py)
import asyncio
import threading
import time
import types
import unittest
from unittest import mock

from bot import pauses, postbox, posts
from tests import tmuxguard  # noqa: F401  (no test reaches a real tmux server)
from tests import test_notice_bugs, test_pauses, test_post_wait

NOW = test_pauses.NOW


class ThroughDcctl(object):
    """dcctl post (the real one, tools/dcctl) writes the spool; a bot thread takes the post from it and handles it as
    the bot does (Posts.safely(): its receipt written at the end); dcctl prints what the receipt says."""
    def through_dcctl(self, post):
        w = test_post_wait.Base("run")
        w.setUp()
        self.addCleanup(w.doCleanups)
        seen = {}

        def bot():
            deadline = time.time() + 5
            while time.time() < deadline:
                got = postbox.waiting(w.spool)
                if got:
                    (path, spooled), = got
                    seen["post"] = dict(spooled)
                    asyncio.run(self.p.safely(spooled, path))
                    return
                time.sleep(0.05)
        t = threading.Thread(target=bot)
        t.start()
        code, out, err = w.run_post(post)
        t.join()
        return code, out, err, seen.get("post")


class Paused(ThroughDcctl, test_pauses.Base):
    def setUp(self):
        super().setUp()
        mock.patch.stopall()  # (Base fixes time.time itself: dcctl's wait would never end; pauses alone keeps it)
        for patch in (mock.patch.object(pauses, "clock", lambda ts: "@%d" % int(ts)),
                      mock.patch.object(pauses, "time", types.SimpleNamespace(time=lambda: self.now))):
            patch.start()
            self.addCleanup(patch.stop)
        self.pause()
        self.bot.tmux.given.clear()

    def test_what_dcctl_writes_has_no_wait_but_a_receipt(self):
        code, out, err, spooled = self.through_dcctl({"channel": "2", "text": "review 好了", "from_channel": "1",
                                                      "wait": 10})
        self.assertNotIn("wait", spooled)
        self.assertTrue(spooled.get("receipt"))

    def test_a_held_post_is_said_through_the_receipt_with_its_message_id(self):
        code, out, err, _ = self.through_dcctl({"channel": "2", "text": "review 好了", "from_channel": "1", "wait": 10})
        self.assertIn(code, (0, None), err)
        lines = out.splitlines()
        self.assertIn("#st-0 暫停到 @%d（用量到線），這則已經留著" % (NOW + 3600), lines[0])
        self.assertTrue(lines[-1].isdigit(), "the message's ID still on the last line (C-87)")
        self.assertEqual(self.given_to(1), [], "said once, in the receipt: not typed into the sender as well")
        self.assertEqual(len(self.bot.channels_state["2"]["paused"]["held"]), 1)

    def test_one_past_the_most_held_is_refused_through_the_receipt(self):
        for i in range(pauses.MOST_HELD):
            self.handled({"channel": "st-0", "text": "m%d" % i, "from_channel": "1"})
        self.bot.tmux.given.clear()
        code, out, err, _ = self.through_dcctl({"channel": "2", "text": "one too many", "from_channel": "1", "wait": 10})
        self.assertEqual(code, 4)
        self.assertIn("這則沒有留住", err)
        self.assertEqual(self.given_to(1), [])

    def test_without_wait_its_claude_is_told_in_its_channel_as_before(self):
        code, out, err, spooled = self.through_dcctl({"channel": "2", "text": "x", "from_channel": "1"})
        self.assertNotIn("receipt", spooled)
        self.assertIn("已送出到 #2", out)
        for _ in range(3):  # (told in a task of its own: posts.later())
            if self.given_to(1):
                break
            time.sleep(0.05)
        told, = self.given_to(1)
        self.assertTrue(told.startswith("（機器人通知）#st-0 暫停到"), told)


class TalkLimitFull(ThroughDcctl, unittest.TestCase):
    setUp, handled = test_notice_bugs.HeldBehind.setUp, test_notice_bugs.HeldBehind.handled  # (not its tests)

    def test_one_past_the_most_held_is_refused_through_the_receipt(self):
        with mock.patch.object(posts, "MOST_HELD", 3):
            for i in range(5):
                self.handled("m%d" % i)
            self.assertEqual(len(self.p.queue_at(2)), 3)
            self.bot.tmux.given.clear()
            code, out, err, spooled = self.through_dcctl({"channel": "2", "text": "m5", "from_channel": "1",
                                                          "verified": True, "wait": 10})
        self.assertNotIn("wait", spooled)
        self.assertEqual(code, 4, (out, err))
        self.assertIn("也沒有留住：停在來回上限、等使用者決定的訊息已經有 3 則", err)
        self.assertTrue(out.splitlines()[-1].isdigit(), "on Discord all the same: its ID")
        self.assertEqual([t for cid, t in self.bot.tmux.given if cid == 1], [], "said in the receipt, not typed in")

    def test_a_held_one_still_gets_its_receipt_when_shown(self):
        """Over the limit with room left: held behind the card, the receipt as before (on Discord: its ID)."""
        with mock.patch.object(posts, "MOST_HELD", 3):
            for i in range(3):
                self.handled("m%d" % i)
            code, out, err, _ = self.through_dcctl({"channel": "2", "text": "m3", "from_channel": "1",
                                                    "verified": True, "wait": 10})
        self.assertIn(code, (0, None), err)
        self.assertIn("已送出到 #2", out)


if __name__ == "__main__":
    unittest.main()
