"""C-99a: a usage limit, and Claude Code's own wait for its reset (bot/quota.py): what the channel and #audit are told
for each way the wait goes, that no 「再試一次」 is offered while it waits, that an idle window that waits is not
closed, and the samples kept (masked, without what Claude said)."""
import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))))
from altention import testing as _testing  # noqa: E402
_testing.guard_home()  # tests never touch the real home (altention/testing.py; 11-08: one left a real dcremote-owner)
import asyncio
import json
import os
import shutil
import subprocess
import tempfile
import time
import unittest
from unittest import mock

import bot  # noqa: F401  (first: it puts the engine's folder on the path)
from altention.connectors.claude_code import hooks, screen
from altention.tests.connectors.claude_code.test_quota import ARMED, REAL, SOON, STALE, under
from bot import blocked, config, quota, tmux_bridge, tmuxio
from tests import tmuxguard  # noqa: F401,E402  (no test reaches a real tmux server)
from tests.test_turn_kinds import P, play, real_sleep

LIMIT = {"hook_event_name": "StopFailure", "error": "rate_limit", "session_id": "s1",
         "last_assistant_message": "You've hit your limit · resets 5am (Asia/Taipei)"}
FIRED = {"hook_event_name": "Notification", "notification_type": "quota_auto_resume_fired", "session_id": "s1",
         "message": "Usage limit available — Claude is continuing your task"}
STALE_EV = {"hook_event_name": "Notification", "notification_type": "quota_auto_resume_stale", "session_id": "s1",
            "message": "Usage limit reset — press enter to continue"}
STOPPED = {"hook_event_name": "Notification", "notification_type": "quota_auto_resume_disabled", "session_id": "s1",
           "message": "Automatic continue stopped after repeated usage-limit hits — the task will not resume on its own"}


def showing(*lines):
    async def screens(st):
        return under(lines), "\x1b[0m" + under(lines)
    return screens


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="quota ")
        self.addCleanup(self.tmp.cleanup)
        for p in (mock.patch.object(config, "STATE", self.tmp.name), mock.patch.object(quota, "LOOKS", (0, 0, 0, 0)),
                  mock.patch.dict(os.environ, {"TZ": "Asia/Taipei"})):
            p.start()
            self.addCleanup(p.stop)
        time.tzset()
        self.addCleanup(time.tzset)

    def samples(self):
        folder = quota.samples_dir()
        out = []
        for name in sorted(os.listdir(folder)) if os.path.isdir(folder) else []:
            with open(os.path.join(folder, name), encoding="utf-8") as f:
                out.append(json.load(f))
        return out


class Waiting(Base):
    def test_claude_code_waits_so_the_bot_says_so_and_offers_no_retry(self):
        rows, st, rt, bridge, printed = play([P("go"), LIMIT], screens=showing(*ARMED))
        (said,) = rows[1][0]
        self.assertTrue(said.startswith("⏸️ **用量達到上限**，Claude Code 會在 <t:"), said)
        self.assertIn(":t> 自己接著做。\n-# 在這裡打字會先送出你的訊息，等待就取消了", said)
        self.assertNotIn("中斷了", said)
        self.assertNotIn("retry:", json.dumps([getattr(m, "view", None) and [c.custom_id for c in m.view.children]
                                               for m in bridge.host.ch.sent], default=str))
        b = st["blocked"]
        self.assertEqual((b["kind"], b["msg"], b["detail"]), ("usage_limit", bridge.host.ch.sent[-1].id, LIMIT["last_assistant_message"]))
        self.assertEqual(time.strftime("%H:%M", time.localtime(b["until"])), "05:00")
        self.assertNotIn("machine", b, "the bot's own machine")
        self.assertTrue(quota.holding(st))
        self.assertTrue(any("用量達到上限，Claude Code 會在 <t:" in a for a in bridge.host.audits), bridge.host.audits)

    def test_soon_counts_as_waiting_and_a_clock_without_its_time_is_said_as_resetting(self):
        rows = play([P("go"), LIMIT], screens=showing(*SOON))[0]
        self.assertIn("會在 <t:", rows[1][0][0], "the status line's reset time is used")
        rows = play([P("go"), LIMIT], screens=showing("Continuing automatically when it resets · esc to cancel"))[0]
        self.assertIn("Claude Code 會在 when it resets 自己接著做", rows[1][0][0])

    def test_no_wait_on_the_screen_is_the_failure_as_ever(self):
        for screens in (None, showing("? for shortcuts"), showing(*STALE)):
            rows, st, rt, bridge, printed = play([P("go"), LIMIT], screens=screens)
            self.assertTrue(rows[1][0][-1].startswith("⚠️ **這一輪中斷了：用量達到上限**"), rows[1][0])
            self.assertEqual(st["blocked"]["kind"], "api_error", "Claude Code waits for nothing: the failure's (manager 2026-10-26)")
            self.assertFalse(quota.holding(st))

    def test_another_failure_is_not_looked_at(self):
        calls = []

        async def screens(st):
            calls.append(st)
            return under(ARMED), ""
        rows = play([P("go"), dict(LIMIT, error="overloaded")], screens=screens)[0]
        self.assertTrue(rows[1][0][-2].startswith("⚠️ **這一輪中斷了"))
        self.assertTrue(rows[1][0][-1].startswith("⚠️ 這一輪中斷了，要你處理：API 錯誤"), "C-103: the owner's mention under it")
        self.assertEqual(calls, [])


class Ending(Base):
    def test_resumed_edits_the_message(self):
        rows, st, rt, bridge, printed = play([P("go"), LIMIT, FIRED], screens=showing(*ARMED))
        self.assertEqual(rows[2][1], [], "no menu: a notice is no dialog")
        self.assertNotIn("blocked", st)
        msg = bridge.host.ch.sent[-1]
        self.assertEqual(msg.content, "▶️ 用量重置了，Claude 接著做。")
        self.assertIn("用量重置，Claude 接著做", bridge.host.audits[-1])

    def test_stale_says_to_press_enter_and_presses_nothing(self):
        rows, st, rt, bridge, printed = play([P("go"), LIMIT, STALE_EV], screens=showing(*ARMED))
        self.assertEqual(rows[2][0], ["⏸️ **用量已經重置**，但 Claude Code 還在等人按 Enter（例如電腦睡著過）：在這裡用 `/key Enter`，或在終端機按 Enter。"])
        self.assertEqual(rows[2][1], [])
        self.assertIn("等人按 Enter", bridge.host.audits[-1])
        self.assertEqual((st["blocked"]["kind"], st["blocked"]["detail"]), ("usage_limit", STALE_EV["message"]))
        self.assertNotIn("until", st["blocked"])

    def test_stopped_is_said_with_its_words_and_a_retry(self):
        rows, st, rt, bridge, printed = play([P("go"), LIMIT, STOPPED], screens=showing(*ARMED))
        (said,) = rows[2][0]
        self.assertEqual(said, "⚠️ **沒有自動接著做**：\n> " + STOPPED["message"] + "\n-# 按「再試一次」會請 Claude 從中斷的地方接著做")
        self.assertEqual(bridge.host.ch.sent[-1].view.children[0].custom_id, "retry:%d" % bridge.host.ch.id)
        self.assertEqual((st["blocked"]["kind"], st["blocked"]["detail"]), ("usage_limit", STOPPED["message"]))

    def test_input_ends_the_hold(self):
        rows, st, rt, bridge, printed = play([P("go"), LIMIT, P("typed meanwhile")], screens=showing(*ARMED))
        self.assertNotIn("blocked", st)
        self.assertFalse(quota.holding(st))
        rows, st, rt, bridge, printed = play([P("go"), LIMIT, P("typed meanwhile"), FIRED], screens=showing(*ARMED))
        self.assertEqual(rows[3][0], ["▶️ 用量重置了，Claude 接著做。"], "the session moved on: a new message")
        self.assertIn("⏸️ **用量達到上限**", bridge.host.ch.sent[1].content, "the wait's message is left as it was")
        self.assertNotIn("limit_msg", st)

    def test_an_earlier_wait_s_message_is_not_edited_for_a_later_one(self):
        """The first wait ended by typing; the second is not seen on the screen (another machine's, unread over
        SSH), though Claude Code waits: at the reset the first ⏸️ is left as it was (#code-review on 1f3f11d)."""
        looks = []

        async def screens(st):
            looks.append(1)
            return (under(*[ARMED]) if len(looks) == 1 else ""), ""
        stop = {"hook_event_name": "Stop", "last_assistant_message": "done.", "session_id": "s1"}
        rows, st, rt, bridge, printed = play([P("go"), LIMIT, P("hello"), stop, P("again"), LIMIT, FIRED], screens=screens)
        first = [m for m in bridge.host.ch.sent if m.content.startswith("⏸️")]
        self.assertEqual(len(first), 1)
        self.assertTrue(first[0].content.startswith("⏸️ **用量達到上限**"), "not edited")
        self.assertTrue(rows[5][0][-1].startswith("⚠️ **這一輪中斷了：用量達到上限**"))
        self.assertEqual(rows[6][0], ["▶️ 用量重置了，Claude 接著做。"])
        self.assertNotIn("limit_msg", st)

    def test_a_failure_not_waited_on_forgets_an_earlier_wait_s_message(self):
        looks = []

        async def screens(st):
            looks.append(1)
            return (under(*[ARMED]) if len(looks) == 1 else ""), ""
        rows, st, rt, bridge, printed = play([P("go"), LIMIT, LIMIT], screens=screens)
        self.assertNotIn("limit_msg", st)


class RealScreen(Base):
    """C-99a end to end: a fake Claude in a real (throwaway) tmux server prints what 2.1.310 showed at the weekly
    limit of 2026-10-26; the bot reads it through tmuxio as it reads any pane."""

    def setUp(self):
        Base.setUp(self)
        if not shutil.which("tmux"):
            self.skipTest("tmux not installed")
        # Its own short socket folder: tmux falls back to the usual one when TMUX_TMPDIR is not there (the deploy
        # suites name one they never make), and a long path is too long for a socket
        self.sockets = tempfile.mkdtemp(prefix="dcq", dir="/tmp")
        self.addCleanup(shutil.rmtree, self.sockets, True)
        p = mock.patch.dict(os.environ, {"TMUX_TMPDIR": self.sockets})
        p.start()
        self.addCleanup(p.stop)
        self.name, self.was = "dcbot-test-%d-q" % os.getpid(), dict(tmuxio._set)
        tmuxio.configure(socket="dcbot-test-%d-q" % os.getpid())
        self.addCleanup(self.stop)

    def stop(self):
        subprocess.run(["tmux", "-L", self.name, "kill-server"], stderr=subprocess.DEVNULL, timeout=10)
        try:
            os.remove(os.path.join(tmuxio.socket_dir(), self.name))
        except OSError:
            pass
        tmuxio._set.clear()
        tmuxio._set.update(self.was)

    def fake_claude(self, sample):
        """A session 120x50 whose one program prints the sample and waits."""
        path = os.path.join(REAL, sample + ".txt")
        r = subprocess.run(["tmux", "-L", self.name, "new-session", "-d", "-s", "dc-q", "-x", "120", "-y", "50",
                            "cat '%s'; exec sleep 600" % path], capture_output=True, text=True, timeout=10)
        self.assertEqual(r.returncode, 0, r.stderr)
        for _ in range(50):
            if "❯" in subprocess.run(["tmux", "-L", self.name, "capture-pane", "-p", "-t", "=dc-q:"],
                                     capture_output=True, text=True, timeout=10).stdout:
                return
            time.sleep(0.1)
        self.fail("the fake Claude printed nothing")

    async def read(self, st):
        return await quota.Quota.screens(None, {"tmux": "dc-q"})  # (the bot's own reading of the pane)

    def test_the_captured_wait_is_read_through_tmux_and_said(self):
        self.fake_claude("armed")
        rows, st, rt, bridge, printed = play([P("go"), LIMIT, P(hooks.QUOTA_CONTINUE + " Continue the task …"), FIRED],
                                             screens=self.read)
        self.assertTrue(rows[1][0][0].startswith("⏸️ **用量達到上限**，Claude Code 會在 <t:"), rows[1][0])
        self.assertEqual(rows[2][0], [], "Claude Code's own prompt at the reset is not echoed as typed at the terminal")
        self.assertEqual(rows[3][0], [], "the ⏸️ message is edited")
        self.assertEqual([m.content for m in bridge.host.ch.sent if m.content.startswith(("⏸️", "▶️"))], ["▶️ 用量重置了，Claude 接著做。"])
        (sample, _) = self.samples()
        self.assertIn("Continuing automatically at 5am", sample["screen"])
        self.assertNotIn("You've hit your weekly limit", sample["screen"] + sample["colour"], "the conversation is not kept")
        self.assertIn("Continuing automatically at 5am", sample["colour"])

    def test_soon_too(self):
        self.fake_claude("soon")
        rows = play([P("go"), LIMIT], screens=self.read)[0]
        self.assertIn("自己接著做", rows[1][0][0])


class AsItHappened(Base):
    """In the order of 2026-10-26, with nothing waited for between events (as the pump waits for none): the
    Notification at the reset, Claude Code's own prompt right after it. That prompt clears `blocked` while the
    Notification is still being worked on (its screen being read); the message is edited all the same
    (#code-review-2 on 9eedf30, manager)."""

    def test_the_message_is_edited_though_the_prompt_came_first(self):
        async def slow(st):
            for _ in range(5):
                await real_sleep(0)  # (reading a screen takes a while: over SSH, much longer)
            return under(*[ARMED]), ""
        continuing = P(hooks.QUOTA_CONTINUE + " Continue the task you were working on when the limit was reached.")
        rows, st, rt, bridge, printed = play([P("go"), LIMIT, FIRED, continuing], screens=slow, settle={1, 2})
        sent = [m.content for m in bridge.host.ch.sent]
        self.assertEqual([c for c in sent if c.startswith(("⏸️", "▶️"))], ["▶️ 用量重置了，Claude 接著做。"],
                         "one message, edited: no second one, no stale ⏸️ left")
        self.assertNotIn("blocked", st)
        self.assertNotIn("limit_msg", st)

    def test_its_first_tool_call_too_comes_before_the_screen_is_read(self):
        async def slow(st):
            for _ in range(5):
                await real_sleep(0)
            return under(*[ARMED]), ""
        continuing = P(hooks.QUOTA_CONTINUE + " Continue the task you were working on when the limit was reached.")
        tool = {"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": "ls"}, "session_id": "s1"}
        rows, st, rt, bridge, printed = play([P("go"), LIMIT, FIRED, continuing, tool], screens=slow, settle={1, 2})
        sent = [m.content for m in bridge.host.ch.sent]
        self.assertEqual([c for c in sent if c.startswith(("⏸️", "▶️"))], ["▶️ 用量重置了，Claude 接著做。"],
                         "the message to edit was taken with the Notification")

    def test_a_reset_worked_on_late_keeps_a_wait_begun_since(self):
        """The reset's screen is read slowly (over SSH); meanwhile Claude Code carries on and hits the limit again,
        and waits. The first reset, done last, edits its own message and leaves the second wait held: shown on the
        board, its window not closed (#code-review on b2ad60e)."""
        reads = []

        async def screens(st):
            reads.append(1)
            if len(reads) == 2:  # (the Notification's)
                for _ in range(60):
                    await real_sleep(0)
            return under(*[ARMED]), ""
        continuing = P(hooks.QUOTA_CONTINUE + " Continue the task you were working on when the limit was reached.")
        tool = {"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": "ls"}, "session_id": "s1"}
        rows, st, rt, bridge, printed = play([P("go"), LIMIT, FIRED, continuing, tool, LIMIT], screens=screens, settle={1, 5})
        waits = [m for m in bridge.host.ch.sent if m.content.startswith(("⏸️", "▶️"))]
        self.assertEqual([m.content[:2] for m in waits], ["▶️", "⏸️"], "the first edited, the second as it was")
        self.assertEqual((st["blocked"]["kind"], st["blocked"]["msg"]), ("usage_limit", waits[1].id))
        self.assertEqual(st["limit_msg"], waits[1].id)
        self.assertTrue(quota.holding(st))

    def test_a_failure_not_waited_on_is_not_said_once_the_session_moved_on(self):
        """Not waiting is known only at the last look, 15 s on; somebody typed meanwhile and that turn ended: no
        「中斷了」 and 「再試一次」 after its reply (#code-review-2 on b2ad60e)."""
        async def slow(st):
            for _ in range(20):
                await real_sleep(0)
            return under(("? for shortcuts",)), ""
        stop = {"hook_event_name": "Stop", "last_assistant_message": "done again", "session_id": "s1"}
        rows, st, rt, bridge, printed = play([P("go"), LIMIT, P("typed meanwhile"), stop], screens=slow, settle=set())
        sent = [m.content for m in bridge.host.ch.sent]
        self.assertFalse([c for c in sent if "中斷了" in c], sent)
        self.assertIn("done again", sent[-1])
        self.assertNotIn("blocked", st, "no api_error written either")
        self.assertFalse(any(m.view for m in bridge.host.ch.sent if getattr(m, "view", None)), "no 「再試一次」")
        bridge = play([P("go"), LIMIT], screens=slow, settle=set())[3]
        self.assertTrue(bridge.host.ch.sent[-1].content.startswith("⚠️ **這一輪中斷了：用量達到上限**"), "nothing moved on: said as ever")

    def test_the_wait_drawn_at_the_tenth_second_is_still_seen(self):
        looks = []

        async def late(st):
            looks.append(len(looks))
            return (under(*[ARMED]) if len(looks) >= 3 else under(("? for shortcuts",))), ""
        rows = play([P("go"), LIMIT], screens=late)[0]
        self.assertIn("⏸️ **用量達到上限**", rows[1][0][0])
        self.assertEqual(len(looks), 3, "read at the first three looks, then no more")
        del looks[:]
        slept = []

        async def sleep(seconds):
            slept.append(seconds)
        q = quota.Quota(None)
        q.screens = late
        with mock.patch.object(quota, "LOOKS", (2, 5, 10, 15)), mock.patch.object(quota.asyncio, "sleep", sleep):
            view, _ = asyncio.run(q.looked({}))
        self.assertEqual(slept, [2, 3, 5], "at 2, 5 and 10 s after the failure")
        self.assertEqual(screen.quota_wait(view)["phase"], "armed")

    def test_none_by_fifteen_seconds_is_the_failure_as_ever(self):
        looks = []

        async def never(st):
            looks.append(1)
            return under(("? for shortcuts",)), ""
        rows = play([P("go"), LIMIT], screens=never)[0]
        self.assertTrue(rows[1][0][-1].startswith("⚠️ **這一輪中斷了：用量達到上限**"))
        self.assertEqual(len(looks), 4)


class Holding(Base):
    def test_holding_lasts_a_day_and_input_ends_it(self):
        now = 1000000
        self.assertTrue(quota.holding({"blocked": {"kind": "usage_limit", "since": now - 3600, "msg": 5}}, now))
        self.assertTrue(quota.holding({"blocked": {"kind": "usage_limit", "since": now - 3600, "until": now + 60}}, now))
        self.assertFalse(quota.holding({"blocked": {"kind": "usage_limit", "since": now - 3600}}, now), "no wait was seen")
        self.assertFalse(quota.holding({"blocked": {"kind": "usage_limit", "since": now - quota.WAIT_HOLD - 1, "msg": 5}}, now))
        self.assertFalse(quota.holding({"blocked": {"kind": "api_error", "since": now}}, now))
        self.assertFalse(quota.holding({"blocked": "x"}, now))
        self.assertFalse(quota.holding({}, now))

    def test_reap_leaves_a_waiting_window_alone(self):
        class Host(object):
            channels_state = {"1": {"tmux": "dc-a", "last_active": 0, "blocked": {"kind": "usage_limit", "since": time.time(), "msg": 5}},
                              "2": {"tmux": "dc-b", "last_active": 0}}
            audits = []

            async def audit(self, text):
                self.audits.append(text)
        bridge = tmux_bridge.TmuxBridge.__new__(tmux_bridge.TmuxBridge)
        bridge.host = Host()
        killed = []

        async def alive(name):
            return True

        async def kill(name):
            killed.append(name)

        async def none():
            return None
        bridge.reap_ownerless = none
        with mock.patch.object(tmux_bridge.tmuxio, "alive", alive), mock.patch.object(tmux_bridge.tmuxio, "kill", kill):
            asyncio.run(bridge.reap())
        self.assertEqual(killed, ["dc-b"])


class Blocked(Base):
    def test_one_shape_one_at_a_time(self):
        st = {}
        blocked.put(st, "dialog", detail="  Claude needs\n your permission ", msg=7, now=100)
        self.assertEqual(st["blocked"], {"kind": "dialog", "since": 100, "detail": "Claude needs your permission", "msg": 7})
        blocked.put(st, "login", "lap", until=200.5, now=101)
        self.assertEqual(st["blocked"], {"kind": "login", "since": 101, "machine": "lap", "until": 200})
        self.assertIsNone(blocked.clear(st, "dialog"), "only the kind asked for")
        self.assertEqual(blocked.clear(st)["kind"], "login")
        self.assertNotIn("blocked", st)
        self.assertIsNone(blocked.of({"blocked": {"since": 1}}), "no kind: nothing")

    def test_detail_is_masked_and_short(self):
        st = {}
        blocked.put(st, "api_error", detail="key sk-ant-api03-" + "B" * 90 + " " + "x" * 500)
        self.assertNotIn("B" * 20, st["blocked"]["detail"])
        self.assertLessEqual(len(st["blocked"]["detail"]), blocked.DETAIL_MAX)

    def test_a_failure_and_a_dialog_hold_up_and_moving_on_clears(self):
        rows, st, rt, bridge, printed = play([P("go"), dict(LIMIT, error="overloaded")])
        self.assertEqual((st["blocked"]["kind"], st["blocked"]["detail"]), ("api_error", "API 錯誤（overloaded）"))
        rows, st, rt, bridge, printed = play([P("go"), dict(LIMIT, error="overloaded"), P("again")])
        self.assertNotIn("blocked", st)


class Clock(Base):
    def test_clock_times_are_the_next_one_here(self):
        at = quota.clock_at("at 5:00am", now=1761415200)  # 2025-10-26 02:00 in Taipei
        self.assertEqual(time.strftime("%Y-%m-%d %H:%M", time.localtime(at)), "2025-10-26 05:00")
        at = quota.clock_at("1:30pm", now=1761450000)     # 11:40 that day
        self.assertEqual(time.strftime("%H:%M", time.localtime(at)), "13:30")
        at = quota.clock_at("5am", now=1761451200)        # 12:00: tomorrow's
        self.assertEqual(time.strftime("%d %H:%M", time.localtime(at)), "27 05:00")
        for text in ("Oct 27 at 5am", "when it resets", "", "13:00pm", "5:61am"):
            self.assertIsNone(quota.clock_at(text, now=1761415200), text)

    def test_another_machines_clock_is_said_as_written(self):
        self.assertEqual(quota.shown_when("at 5:00am", here=False), "5:00am")
        self.assertTrue(quota.shown_when("at 5:00am", here=True).startswith("<t:"))
        self.assertEqual(quota.shown_when("when it resets", here=True), "when it resets")


class Samples(Base):
    def test_each_event_keeps_a_masked_sample_without_what_claude_said(self):
        secret = "sk-ant-api03-" + "A" * 90
        armed = showing(ARMED[0], ARMED[1] + " " + secret)
        play([P("go"), dict(LIMIT, texts=["private words"], transcript_path="/p/t.jsonl"), FIRED], screens=armed)
        got = self.samples()
        self.assertEqual([s["event"]["dc_kind"] for s in got], ["turn_failed", "quota_resumed"])
        whole = json.dumps(got, ensure_ascii=False)
        self.assertNotIn(secret, whole)
        self.assertNotIn("private words", whole)
        self.assertNotIn("/p/t.jsonl", whole)
        self.assertIn("You've hit your limit", got[0]["event"]["last_assistant_message"])
        self.assertIn("Continuing automatically", got[0]["screen"])
        self.assertNotIn("❯", got[0]["screen"], "only the rows under the box: not the box, not the conversation")
        self.assertLessEqual(len(got[0]["screen"].split("\n")), 4)

    def test_only_the_last_are_kept(self):
        with mock.patch.object(quota, "SAMPLES_KEPT", 3):
            for i in range(5):
                quota.save_sample({"dc_kind": "quota_stale", "channel": str(i)}, "s", "c", now=1000 + i)
        self.assertEqual([s["event"]["channel"] for s in self.samples()], ["2", "3", "4"])


if __name__ == "__main__":
    unittest.main()
