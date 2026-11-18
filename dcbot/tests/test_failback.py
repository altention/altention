"""C-163 step 3c: failing back to the primary (design section 5) and every machine told where the bot is (4.4)."""
import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))))
from altention import testing as _testing  # noqa: E402
_testing.guard_home()  # tests never touch the real home (altention/testing.py; 11-08: one left a real dcremote-owner)
import asyncio
import contextlib
import datetime
import io
import json
import os
import tempfile
import types
import unittest
from unittest import mock

from bot import lease, main, standby
from tests import tmuxguard  # noqa: F401  (no test reaches a real tmux server)

FOUND = {"socket": "/t/b/dcbot", "panes": [{"session": "dc-new-0021", "id": "%9", "pid": 5, "socket": "/t/b/dcbot"}]}
NAME = "%020d-1.json"  # a spool file's name (postbox.write()'s shape; written out it looks like an id)
MACHINES = {"host-a": {"roles": ["dcbot"], "ssh": ["a"]}, "host-b": {"roles": ["dcbot-standby"], "ssh": ["b"]},
            "host-c": {"roles": ["dcattach"], "ssh": ["c"]}}



_PATCHES = []


def setUpModule():
    """tell_hosts() writing this machine raises its dcremote-owner and drops older routes: never in the real HOME or
    the engine's own route table (11-08: a run of this file left the real ~/.local/share/altention/dcremote-owner)."""
    from bot import routecore
    _PATCHES.extend([mock.patch.object(main.config_mod, "raise_dcremote_owner", lambda epoch: True),
                     mock.patch.object(routecore, "drop_older", lambda epoch, named=(): 0)])
    for p in _PATCHES:
        p.start()


def tearDownModule():
    for p in _PATCHES:
        p.stop()

class Channels(unittest.TestCase):
    def test_each_conversation_where_it_is_now(self):
        channels = {
            # the primary's, taken over (a window of its bot server still up)
            "11": {"home": {"host": "host-a", "socket": "/a/dcbot", "session": "dc-talk-0011", "window": ""},
                   "pane": {"host": "host-a", "socket": "/a/dcbot", "id": "%3"}, "session_id": "s1",
                   "took_from": {"machine": "host-a", "tmux": "dc-talk-0011"}},
            # the primary's, a window in the owner's tmux there; once print mode
            "12": {"home": {"host": "host-a", "socket": "/u", "session": "w"}, "mode": "tmux",
                   "took_from": {"machine": "host-a", "home": True, "mode": "print"}},
            # the primary's, never reached
            "13": {"tmux": "dc-x-0013", "took_from": {"machine": "host-a", "pending": True}},
            # the primary's, never opened before: opened there during the takeover
            "14": {"home": {"host": "host-a", "socket": "/a/dcbot", "session": "dc-y-14", "window": ""},
                   "took_from": {"machine": "host-a", "tmux": None}},
            # this machine's own (4.3)
            "15": {"home": {"socket": "/u", "session": "w"}, "pane": {"socket": "/u", "id": "%1"},
                   "took_from": {"local": ["home", "pane"], "machine": "host-b"}},
            # opened here during the takeover, the bot's own server here
            "21": {"tmux": "dc-new-0021", "session_id": "s21", "cwd": "/w", "mode": "print"},
            # a files channel made here
            "22": {"kind": "files", "files": {"root": "/w"}},
            # never talked to yet
            "23": {"kind": "conversation", "cwd": "/w"},
            # another machine's: as it is
            "31": {"home": {"host": "host-c", "socket": "/c", "session": "w"}, "session_id": "s31"},
        }
        got = standby.failback_channels(channels, "host-b", "host-a", FOUND)
        self.assertEqual(got["11"], {"tmux": "dc-talk-0011", "session_id": "s1"}, "back to its own bot server")
        self.assertEqual(got["12"], {"home": {"socket": "/u", "session": "w"}, "mode": "print"})
        self.assertEqual(got["13"], {"tmux": "dc-x-0013"})
        self.assertEqual(got["14"], {"tmux": "dc-y-14"})
        self.assertEqual(got["15"], {"home": {"host": "host-b", "socket": "/u", "session": "w"},
                                     "pane": {"host": "host-b", "socket": "/u", "id": "%1"}})
        self.assertEqual(got["21"]["home"], {"host": "host-b", "socket": "/t/b/dcbot", "session": "dc-new-0021", "window": ""},
                         "stays here, named: its session file is on this disk")
        self.assertEqual(got["21"]["pane"], {"host": "host-b", "socket": "/t/b/dcbot", "id": "%9", "pid": 5})
        self.assertNotIn("tmux", got["21"])
        self.assertEqual(got["21"]["mode"], "tmux")
        self.assertNotIn("cc_stale", json.dumps(got), "never marked stale (manager, #code-review must 2)")
        self.assertEqual(got["22"]["files"], {"root": "/w", "host": "host-b"})
        self.assertEqual(got["23"], {"kind": "conversation", "cwd": "/w"}, "nothing yet: opened on the primary")
        self.assertEqual(got["31"], channels["31"])
        self.assertIn("took_from", channels["11"], "a copy: this bot's own state untouched")

    def test_bundle_carries_the_channels_and_the_mark(self):
        state = tempfile.mkdtemp()
        with open(os.path.join(state, "channels.json"), "w") as f:
            f.write('{"old": 1}')
        with open(os.path.join(state, "seen.json"), "w") as f:
            f.write("{}")
        target = os.path.join(tempfile.mkdtemp(), "state-standby")
        standby.unpack(standby.failback_bundle(state, {"new": 1}, {"at": 7.0}), target)
        with open(os.path.join(target, "channels.json")) as f:
            self.assertEqual(json.load(f), {"new": 1})
        self.assertEqual(standby.failback_waiting(target, 0), 7.0)
        self.assertIsNone(standby.failback_waiting(target, 7.0), "taken in already")
        standby.unpack(standby.bundle(state, {"at": 9.0}), target)
        self.assertIsNone(standby.failback_waiting(target, 0), "an ordinary push is no failback")


T0 = datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc)


class Bot(unittest.TestCase):
    def setUp(self):  # (C-193: posts_out() reads the spool at hand-back; never the real one, wherever this runs)
        spool = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(spool, True))
        p = mock.patch.object(main.postbox, "SPOOL", spool)
        p.start()
        self.addCleanup(p.stop)
        self.spool = spool

    def make(self):
        b = types.SimpleNamespace(channels_state={}, audits=[], gave_up=False, sent=[])
        for name in ("failback_due", "day_stopped", "failback_round", "fail_back", "stop_draining", "posts_out", "posts_back", "failback_pressed", "announce_host", "rebind_far",
                     "tell_hosts", "in_background", "primary_ssh_check", "lease_round"):
            setattr(b, name, types.MethodType(getattr(main.Bot, name), b))
        b.lease = types.SimpleNamespace(entries=[], now=T0, epoch=4)
        real_bg = b.in_background

        def in_background(name, fn):  # (what goes on a task of its own, kept for the tests to look at)
            b.scheduled = getattr(b, "scheduled", []) + [(name, fn)]
            return real_bg(name, fn) if name != "posts-back" else None
        b.in_background = in_background
        b.rounds = 0

        async def round_():
            b.rounds += 1
        b.lease.round = round_
        b.failing_back, b.TELL_SECS, b.DRAIN_SECS = False, main.Bot.TELL_SECS, main.Bot.DRAIN_SECS
        b.leader_ok = lambda: True

        async def audit(text):
            b.audits.append(text)

        async def give_up():
            b.gave_up = True
        async def let_through():
            b.let_go = True
        b.audit, b.give_up, b.let_through = audit, give_up, let_through
        ch = types.SimpleNamespace()

        async def send(text, view=None):
            b.sent.append((text, view))
        ch.send = send

        async def notify_channel():
            return ch
        b.tmux = types.SimpleNamespace(notify_channel=notify_channel)
        return b

    def alive(self, b, secs_ago):
        b.lease.entries = [lease.parse(lease.worded("host-a", 3, False, None), stamp=T0 - datetime.timedelta(seconds=secs_ago))]

    def test_back_for_half_an_hour_without_a_break(self):
        b = self.make()
        b.primary_ssh_ok = True
        self.alive(b, 30)
        self.assertFalse(b.failback_due("host-a", 0))
        self.assertFalse(b.failback_due("host-a", lease.FAILBACK_SECS - 1))
        self.alive(b, lease.ALIVE_SECS + 1)
        self.assertFalse(b.failback_due("host-a", lease.FAILBACK_SECS), "a break: counted again from the start")
        self.alive(b, 30)
        self.assertFalse(b.failback_due("host-a", lease.FAILBACK_SECS + 10))
        self.assertTrue(b.failback_due("host-a", 2 * lease.FAILBACK_SECS + 10))
        b.primary_ssh_ok = False
        self.assertFalse(b.failback_due("host-a", 3 * lease.FAILBACK_SECS), "ssh down: not back")

    def test_never_back_to_a_primary_marked_down(self):
        """Drill item 20 (manager 11-10): the primary's supervisor edits its message each minute while its bot
        crashes; "down" in it is not "back". A message without the field (an older engine's) counts as before."""
        b = self.make()
        b.primary_ssh_ok = True
        b.lease.entries = [lease.parse(lease.worded("host-a", 3, False, None, "crashing"), stamp=T0 - datetime.timedelta(seconds=30))]
        self.assertFalse(b.failback_due("host-a", 0))
        self.assertFalse(b.failback_due("host-a", 2 * lease.FAILBACK_SECS))
        old = '{"machine": "host-a", "epoch": 3, "leader": false, "lt": null}'
        b.lease.entries = [lease.parse(old, stamp=T0 - datetime.timedelta(seconds=30))]
        self.assertFalse(b.failback_due("host-a", 3 * lease.FAILBACK_SECS))
        self.assertTrue(b.failback_due("host-a", 4 * lease.FAILBACK_SECS + 1), "old format: back as before")

    def run_round(self, b, setting="auto", push=0, state=None, clock=10 ** 6, answers=None):
        state = state or tempfile.mkdtemp()
        calls = []

        async def dcremote(host, *args, **kw):
            calls.append((host,) + args + ((kw["input"],) if "input" in kw and args[0] == "spool-in" else ()))
            if args[0] in (answers or {}):
                return answers[args[0]]
            return (push, "") if args[0] == "state-in" else (0, "")
        alt = mock.Mock(setting=lambda k, d=None: setting if k == "dcbot.failback" else d)
        with mock.patch.object(main.remote, "inventory", lambda: {"machines": MACHINES}), \
                mock.patch.object(main.remote, "this_machine", lambda: "host-b"), \
                mock.patch.object(main.remote, "alt", lambda: alt), \
                mock.patch.object(main.remote, "dcremote", dcremote), \
                mock.patch.object(main, "STATE", state), \
                mock.patch.object(main.config_mod, "write_bot_host", lambda m, e: calls.append(("here", m, e)) or True), \
                mock.patch.object(standby, "bot_panes", lambda s: FOUND), \
                mock.patch.object(lease, "clock", lambda: clock):
            b.back_since = clock - lease.FAILBACK_SECS
            b.primary_ssh_ok = True
            got = asyncio.run(b.failback_round())
        return got, calls, state

    def test_auto_fails_back(self):
        b = self.make()
        self.alive(b, 10)
        got, calls, state = self.run_round(b)
        self.assertTrue(got)
        self.assertTrue(b.gave_up)
        self.assertIn(("host-a", "state-in"), [c[:2] for c in calls])
        self.assertIn(("here", "host-a", 4), calls, "bot-host back to the primary here")
        self.assertIn(("host-c", "bot-host", "host-a", "4"), calls, "and on the other machines")
        self.assertEqual(b.sent[0][0], "✅ host-a 回來了，機器人已經切回 host-a")
        with open(os.path.join(state, "failed-back.json")) as f:
            self.assertEqual(json.load(f)["primary"], "host-a")

    def test_a_push_not_taken_changes_nothing(self):  # (and see the next tests)
        b = self.make()
        self.alive(b, 10)
        got, calls, state = self.run_round(b, push=255)
        self.assertFalse(got)
        self.assertFalse(b.gave_up)
        self.assertFalse(os.path.exists(os.path.join(state, "failed-back.json")))
        self.assertTrue(any("沒辦法把目前的狀態交給 host-a" in a for a in b.audits))

    def test_nothing_handled_here_while_handing_back(self):
        """#code-review, #code-review-2 on 8778f8d (manager): from the start of fail_back() this machine handles
        nothing: leader_ok() is false, a message arriving meanwhile is held (never marked, so not in the seen.json
        pushed; the primary's catch-up reads it), the push rounds stop."""
        b = self.make()
        self.alive(b, 10)
        b.held_back, b.lease.write_ok = [], 1
        b.lease.ok = lambda: True
        b.leader_ok = types.MethodType(main.Bot.leader_ok, b)
        seen_during = []
        state = tempfile.mkdtemp()
        with open(os.path.join(state, "seen.json"), "w") as f:
            f.write('{"7": "100"}')

        async def dcremote(host, *args, input=None, **kw):
            if args[0] == "state-in":
                seen_during.append(main.Bot.leader_ok(b))
                msg = types.SimpleNamespace(content="late")
                seen_during.append(main.Bot.hold(b, msg))  # (on_message's first step with a standby)
                import base64, io, tarfile
                with tarfile.open(fileobj=io.BytesIO(base64.b64decode(input)), mode="r:gz") as tar:
                    seen_during.append(json.loads(tar.extractfile("seen.json").read()))
            return 0, ""
        alt = mock.Mock(setting=lambda k, d=None: d)
        with mock.patch.object(main.remote, "inventory", lambda: {"machines": MACHINES}), \
                mock.patch.object(main.remote, "this_machine", lambda: "host-b"), \
                mock.patch.object(main.remote, "alt", lambda: alt), \
                mock.patch.object(main.remote, "dcremote", dcremote), \
                mock.patch.object(main, "STATE", state), \
                mock.patch.object(main.config_mod, "write_bot_host", lambda m, e: True), \
                mock.patch.object(standby, "bot_panes", lambda s: FOUND):
            self.assertTrue(asyncio.run(b.fail_back("host-a")))
        self.assertEqual(seen_during[:2], [False, True], "not the bot meanwhile; the message held")
        self.assertEqual(seen_during[2], {"7": "100"}, "seen.json as it was: the late one is the primary's catch-up's")
        self.assertEqual([m.content for _, m in b.held_back], ["late"])

    def test_a_push_not_taken_lets_go_and_waits_ten_minutes(self):
        b = self.make()
        self.alive(b, 10)
        got, calls, state = self.run_round(b, push=255)
        self.assertFalse(got)
        self.assertFalse(b.failing_back)
        self.assertTrue(b.let_go, "what waited goes on")
        self.assertEqual(b.rounds, 1, "a lease round at once (#code-review-2 on 7377217)")
        self.assertGreater(b.failback_retry_at, 0)
        got, calls2, _ = self.run_round(b, push=255, state=state)
        self.assertFalse([c for c in calls2 if c[1:2] == ("state-in",)], "not again within 10 minutes")
        b.failback_retry_at = 0
        self.run_round(b, push=255, state=state)
        self.assertEqual(len([a for a in b.audits if "沒辦法把目前的狀態交給" in a]), 1, "#audit once an hour at most")

    def test_a_post_on_its_way_first(self):
        b = self.make()
        self.alive(b, 10)
        b.posts = types.SimpleNamespace(inflight={"/spool/1.json"})
        got, calls, _ = self.run_round(b)
        self.assertFalse(got)
        self.assertFalse([c for c in calls if c[1:2] == ("state-in",)], "handed back once the post is through")

    def test_posts_that_keep_coming_never_hold_the_failback_off(self):
        """C-188: posts on their way when the bot may go back: new ones go to the primary's spool (bot-host there),
        those here are waited for, DRAIN_SECS at most; then it goes back all the same, saying what is left."""
        b = self.make()
        self.alive(b, 10)
        b.posts = types.SimpleNamespace(inflight={"/spool/1.json"})
        got, calls, state = self.run_round(b)
        self.assertFalse(got)
        self.assertIn(("here", "host-a", 4), calls, "bot-host to the primary: new posts go there")
        self.assertIn(("host-a", "bot-host", "host-a", "4"), [c[:4] for c in calls])
        self.assertFalse([c for c in calls if c[1:2] == ("state-in",)])
        self.assertIn("還有 1 則傳話在送", b.audits[-1])
        got, calls, _ = self.run_round(b, state=state, clock=10 ** 6 + 60)
        self.assertEqual((got, calls), (False, []), "waiting: nothing more")
        b.posts.inflight = set()
        got, calls, _ = self.run_round(b, state=state, clock=10 ** 6 + 90)
        self.assertTrue(got, "through: handed back")
        b = self.make()
        self.alive(b, 10)
        spool = tempfile.mkdtemp()
        late = os.path.join(spool, (NAME % 9))
        with open(late, "w") as f:
            json.dump({"channel": "st-0", "text": "late"}, f)
        b.posts = types.SimpleNamespace(inflight={late})
        self.run_round(b)
        got, calls, _ = self.run_round(b, clock=10 ** 6 + main.Bot.DRAIN_SECS, answers={"spool-in": (0, '{"posts": 1}')})
        self.assertTrue(got, "waited long enough: handed back all the same")
        moved = [c for c in calls if c[1:2] == ("spool-in",)]
        self.assertEqual(json.loads(moved[0][2]), [{"name": (NAME % 9), "post": {"channel": "st-0", "text": "late"}}],
                         "to the primary's spool, its name kept (manager 11-16)")
        self.assertLess(calls.index(moved[0]), max(i for i, c in enumerate(calls) if c[1:2] == ("state-in",)) + 10)
        self.assertFalse(os.path.exists(late), "taken away here")
        self.assertIn("還有 1 則傳話沒送完，搬到 host-a 的佇列，由它接著交", "".join(b.audits))
        b = self.make()
        self.alive(b, 10)
        with open(late, "w") as f:
            json.dump({"channel": "st-0", "text": "late"}, f)
        b.posts = types.SimpleNamespace(inflight={late})
        self.run_round(b)
        got, calls, _ = self.run_round(b, clock=10 ** 6 + main.Bot.DRAIN_SECS, answers={"spool-in": (1, "")})
        self.assertTrue(got)
        self.assertTrue(os.path.exists(late), "not taken there: left here")
        self.assertIn("還有 1 則傳話沒送完（%s），照樣切回" % (NAME % 9), "".join(b.audits))

    def test_posts_here_at_hand_back_go_with_it(self):
        """C-193 (reviews on c1d83b77): what is moved is looked up at hand-back: the spool as it is then and what is on
        its way, with a wait before it or not, so posts come while the state is pushed go too."""
        def put(n):
            path = os.path.join(self.spool, NAME % n)
            with open(path, "w") as f:
                json.dump({"channel": "st-0", "text": "p%d" % n}, f)
            return path
        b = self.make()
        self.alive(b, 10)
        put(7)  # (nothing on its way at the round: no wait; this one came while the state was pushed)
        got, calls, _ = self.run_round(b, answers={"spool-in": (0, '{"posts": 1}')})
        self.assertTrue(got)
        moved = [json.loads(c[2]) for c in calls if c[1:2] == ("spool-in",)]
        self.assertEqual([[i["name"] for i in m] for m in moved], [[NAME % 7]])
        self.assertEqual(os.listdir(self.spool), [])
        self.assertIn("交還時 host-b 的佇列還有 1 則傳話，搬到 host-a 的佇列", b.audits[-2])
        b = self.make()
        self.alive(b, 10)
        late = put(8)
        b.posts = types.SimpleNamespace(inflight={late})
        self.run_round(b)
        put(9)  # (came after the round that started the wait)
        got, calls, _ = self.run_round(b, clock=10 ** 6 + main.Bot.DRAIN_SECS, answers={"spool-in": (0, '{"posts": 2}')})
        self.assertTrue(got)
        moved = [json.loads(c[2]) for c in calls if c[1:2] == ("spool-in",)]
        self.assertEqual([[i["name"] for i in m] for m in moved], [[NAME % 8, NAME % 9]], "both, oldest first")
        self.assertIn("還有 2 則傳話沒送完，搬到 host-a 的佇列", "".join(b.audits))

    def test_draining_called_off(self):
        """The primary gone again, or the push not taken, while posts were waited for: bot-host back here."""
        b = self.make()
        self.alive(b, 10)
        b.posts = types.SimpleNamespace(inflight={"/spool/1.json"})
        self.run_round(b)
        self.alive(b, lease.ALIVE_SECS + 5)
        got, calls, _ = self.run_round(b)
        self.assertFalse(got)
        self.assertIn(("here", "host-b", 4), calls, "posts come here again")
        self.assertIsNone(b.draining)
        self.assertEqual(self.brought_back(b), ["host-a"], "the posts sent there meanwhile brought back (#code-review-2 on c1d83b77)")
        b = self.make()
        self.alive(b, 10)
        b.posts = types.SimpleNamespace(inflight={"/spool/1.json"})
        self.run_round(b)
        b.posts.inflight = set()
        got, calls, _ = self.run_round(b, push=255)
        self.assertFalse(got)
        pushed = [i for i, c in enumerate(calls) if c[1:2] == ("state-in",)]
        self.assertIn(("here", "host-b", 4), calls[pushed[0]:], "the push not taken: back here")
        self.assertEqual(self.brought_back(b), ["host-a"])

    @staticmethod
    def brought_back(b):
        """What stop_draining() put on a task of its own (in_background: posts-back), run now: the machines it asks."""
        asked = []

        async def posts_back(primary):
            asked.append(primary)
        b.posts_back = posts_back
        for name, fn in getattr(b, "scheduled", []):
            if name == "posts-back":
                asyncio.run(fn())
        return asked

    def test_posts_sent_to_the_primary_meanwhile_come_back(self):
        """manager 11-16 (A): the failback called off, the posts written into the primary's spool while bot-host named
        it are moved back here (names kept), then taken away there; what could not be is said in #audit."""
        b = self.make()
        spool = tempfile.mkdtemp()
        there = [{"name": (NAME % 3), "post": {"channel": "st-0", "text": "meanwhile"}},
                 {"name": "../evil.json", "post": {"text": "x"}}]
        for answers, said, left in (({"spool-out": (0, json.dumps(there))}, "已經搬回 host-b", True),
                                    ({"spool-out": (0, json.dumps(there)), "spool-drop": (255, "")}, "沒刪掉", True),
                                    ({"spool-out": (255, "")}, "沒辦法搬回 host-b", False)):
            for n in os.listdir(spool):
                os.remove(os.path.join(spool, n))
            calls = []

            async def dcremote(host, *args, _calls=calls, _answers=answers, **kw):
                _calls.append((host,) + args)
                return _answers.get(args[0], (0, ""))
            with mock.patch.object(main.remote, "dcremote", dcremote), mock.patch.object(main.postbox, "SPOOL", spool), \
                    mock.patch.object(main.remote, "this_machine", lambda: "host-b"):
                asyncio.run(b.posts_back("host-a"))
            self.assertIn(said, b.audits[-1])
            self.assertEqual(sorted(os.listdir(spool)), [(NAME % 3)] if left else [], said)
            if left:
                self.assertEqual(calls[-1], ("host-a", "spool-drop", (NAME % 3)), "only what came")

    def test_a_machine_not_told_back_is_told_again(self):
        """#code-review on 0fd1323e: bot-host turned back here, a machine not reached then is told again by
        announce_host() (once per epoch for each machine: only those done count as told)."""
        b = self.make()
        self.alive(b, 10)
        b.hosts_told = (4, {"host-a", "host-b", "host-c"})  # (told at the takeover)
        b.posts = types.SimpleNamespace(inflight={"/spool/1.json"})
        self.run_round(b)
        self.alive(b, lease.ALIVE_SECS + 5)
        down = {"host-c"}
        calls = []

        async def dcremote(host, *args, **kw):
            calls.append((host,) + args)
            return (255, "") if host in down else (0, "")
        with mock.patch.object(main.remote, "inventory", lambda: {"machines": MACHINES}), \
                mock.patch.object(main.remote, "this_machine", lambda: "host-b"), \
                mock.patch.object(main.remote, "dcremote", dcremote), \
                mock.patch.object(main.config_mod, "write_bot_host", lambda m, e: True), \
                mock.patch.object(lease, "clock", lambda: 10 ** 6):
            b.back_since = None
            asyncio.run(b.failback_round())
            self.assertNotIn("host-c", b.hosts_told[1], "not reached: not told")
            down.clear()
            calls.clear()
            b.rebind_far = lambda epoch: asyncio.sleep(0)
            asyncio.run(b.announce_host())
        self.assertIn(("host-c", "bot-host", "host-b", "4"), [c[:4] for c in calls], "told again: posts come here")
        self.assertNotIn("host-a", [c[0] for c in calls], "the others not again")

    def test_ask_once_with_a_button(self):
        b = self.make()
        self.alive(b, 10)
        got, _, _ = self.run_round(b, setting="ask")
        self.assertFalse(got)
        got, _, _ = self.run_round(b, setting="ask")
        self.assertEqual(len(b.sent), 1, "asked once")
        text, view = b.sent[0]
        self.assertEqual(text, "✅ host-a 回來了，現在由 host-b 在跑。要切回 host-a 嗎？")
        self.assertEqual(view.children[0].custom_id, "failback:4")
        self.assertEqual(view.children[0].label, "切回 host-a")
        self.assertFalse(b.gave_up)

    def test_stopped_for_the_day_stays(self):
        b = self.make()
        self.alive(b, 10)
        state = tempfile.mkdtemp()
        import time
        with open(os.path.join(state, "lease-takeovers.json"), "w") as f:
            json.dump([time.time() - 60] * lease.DAY_MOST, f)
        got, _, _ = self.run_round(b, state=state)
        self.assertFalse(got)
        self.assertFalse(b.gave_up)

    def test_the_primary_itself_never_fails_back(self):
        b = self.make()
        with mock.patch.object(main.remote, "inventory", lambda: {"machines": MACHINES}), \
                mock.patch.object(main.remote, "this_machine", lambda: "host-a"):
            self.assertFalse(asyncio.run(b.failback_round()))

    def test_an_old_button(self):
        b = self.make()
        said = []
        response = mock.Mock(send_message=mock.AsyncMock(side_effect=lambda t, ephemeral: said.append(t)))
        interaction = types.SimpleNamespace(user=types.SimpleNamespace(id=main.config.OWNER_ID),
                                            data={"custom_id": "failback:3"}, response=response)
        with mock.patch.object(main.remote, "inventory", lambda: {"machines": MACHINES}):
            asyncio.run(b.failback_pressed(interaction))
        self.assertEqual(said, ["這個按鈕已經過期了（之後又換過機器），請按最新那則的按鈕。"])

    def test_the_button_pressed_is_taken_off_once_failed_back(self):
        """Drill item 5 (mac, 11-10): the ask's button stayed after the failback. Done: the message says so, no
        button, and before give_up() (its close() ends the connection to Discord: #code-review, #code-review-2 on
        3d57410: the edit after it failed); not done (the push not taken): both as they were, the owner told."""
        for push in (0, 1):
            b = self.make()
            self.alive(b, 10)
            closed, edits, followed = [], [], []

            async def give_up(closed=closed, b=b):
                b.gave_up = True
                closed.append(True)  # (discord.Client.close(): no HTTP request goes after it)

            async def edit(closed=closed, edits=edits, **kw):
                if closed:
                    raise RuntimeError("Session is closed")
                edits.append(kw)
            b.give_up = give_up
            response = mock.Mock(defer=mock.AsyncMock(), send_message=mock.AsyncMock())
            interaction = types.SimpleNamespace(
                user=types.SimpleNamespace(id=main.config.OWNER_ID), message=types.SimpleNamespace(edit=edit),
                data={"custom_id": "failback:%d" % b.lease.epoch}, response=response,
                followup=mock.Mock(send=mock.AsyncMock(side_effect=lambda t, ephemeral, followed=followed: followed.append(t))))

            async def dcremote(host, *args, push=push, **kw):
                return (push, "") if args[0] == "state-in" else (0, "")
            with mock.patch.object(main.remote, "inventory", lambda: {"machines": MACHINES}), \
                    mock.patch.object(main.remote, "this_machine", lambda: "host-b"), \
                    mock.patch.object(main.remote, "dcremote", dcremote), \
                    mock.patch.object(main, "STATE", tempfile.mkdtemp()), \
                    mock.patch.object(main.config_mod, "write_bot_host", lambda m, e: True), \
                    mock.patch.object(standby, "bot_panes", lambda s: FOUND), \
                    contextlib.redirect_stderr(io.StringIO()):
                asyncio.run(b.failback_pressed(interaction))
            if push == 0:
                self.assertEqual(edits, [{"content": "✅ host-a 回來了，已經按下切回，現在由 host-a 在跑。", "view": None}],
                                 "edited before the connection closed")
                self.assertEqual((followed, b.gave_up), ([], True))
            else:
                self.assertEqual((edits, b.gave_up), ([], False), "not failed back: the button stays")
                self.assertEqual(len(followed), 1)

    def test_an_attached_pane_of_this_machine_bound_again_at_a_new_epoch(self):
        """Drill item 12 on the mac (11-10, manager): after a takeover the new bot binds the attached panes of its
        own machine again (the old bot bound them by dcremote, with its epoch), as rebind_far() does elsewhere. A
        window this bot opened here (home) reports through its --settings: no route."""
        from bot import routecore
        b = self.make()
        b.channels_state = {"1": {"pane": {"socket": "/s", "id": "%0", "pid": 20}},           # attached, here
                            "2": {"pane": {"socket": "/s", "id": "%1", "pid": 21, "host": "host-b"}},  # here, marked
                            "3": {"pane": {"socket": "/s", "id": "%2", "pid": 22}, "home": {"socket": "/s"}},  # opened
                            "4": {"pane": {"socket": "/far", "id": "%3", "pid": 23, "host": "host-a"}}}  # elsewhere
        far = []

        async def dcremote(host, *args, **kw):
            far.append((host,) + args)
            return 0, ""
        table = os.path.join(tempfile.mkdtemp(), "routes.json")
        with open(table, "w") as f:  # (what the old bot bound here: its epoch)
            json.dump({"/s|%0": {"channel": "1", "pid": "20", "epoch": 93}}, f)
        with mock.patch.object(routecore, "ROUTES", table), mock.patch.object(main.remote, "dcremote", dcremote), \
                mock.patch.object(main.remote, "this_machine", lambda: "host-b"):
            asyncio.run(b.rebind_far(94))
            asyncio.run(b.rebind_far(94))  # (once per epoch)
            got = routecore.load()
        self.assertEqual(got["/s|%0"], {"channel": "1", "pid": "20"}, "bound again by this bot: no older epoch on it")
        self.assertEqual(got["/s|%1"]["channel"], "2")
        self.assertNotIn("/s|%2", got, "a window this bot opened: no route")
        self.assertEqual(far, [("host-a", "bind", "/far|%3", "4", "23")], "elsewhere as before, once")

    def test_a_pane_of_the_old_bot_machine_not_taken_over_yet_is_left_for_later(self):
        """#code-review-2 on 3272a00: a channel of the old bot machine waits (took_from pending, its pane with no
        host yet): not bound here, and bound on that machine once adopt_one() gave it its host, in the same epoch."""
        from bot import routecore
        b = self.make()
        b.channels_state = {"5": {"pane": {"socket": "/s", "id": "%0", "pid": 20},
                                  "took_from": {"machine": "host-a", "pending": True}}}
        far = []

        async def dcremote(host, *args, **kw):
            far.append((host,) + args)
            return 0, ""
        with mock.patch.object(routecore, "ROUTES", os.path.join(tempfile.mkdtemp(), "routes.json")), \
                mock.patch.object(main.remote, "dcremote", dcremote), mock.patch.object(main.remote, "this_machine", lambda: "host-b"):
            asyncio.run(b.rebind_far(94))
            self.assertEqual((routecore.load(), far), ({}, []), "pending: nowhere yet")
            b.channels_state["5"] = {"pane": {"socket": "/s", "id": "%0", "pid": 20, "host": "host-a"},
                                     "took_from": {"machine": "host-a", "pane": True}}
            asyncio.run(b.rebind_far(94))
            self.assertEqual((routecore.load(), far), ({}, [("host-a", "bind", "/s|%0", "5", "20")]), "then there, once")
            b.channels_state["6"] = {"pane": {"socket": "/s", "id": "%1", "pid": 21}, "took_from": {"local": ["pane"]}}
            asyncio.run(b.rebind_far(94))
            self.assertEqual(routecore.load(), {"/s|%1": {"channel": "6", "pid": "21"}}, "this machine's own (localize_own): here")

    def test_the_standbys_own_route_stays_when_a_pending_pane_has_the_same_key(self):
        """#code-review on 3272a00 (manager): the standby's own attached pane and the old bot machine's pane, not taken
        over yet, at the same "/s|%0" (the same uid's default socket on both): the standby's route stays."""
        from bot import routecore
        b = self.make()
        b.channels_state = {"1": {"pane": {"socket": "/s", "id": "%0", "pid": 20}, "took_from": {"local": ["pane"]}},
                            "5": {"pane": {"socket": "/s", "id": "%0", "pid": 99},
                                  "took_from": {"machine": "host-a", "pending": True}}}
        table = os.path.join(tempfile.mkdtemp(), "routes.json")
        with open(table, "w") as f:
            json.dump({"/s|%0": {"channel": "1", "pid": "20", "epoch": 93}}, f)

        async def dcremote(host, *args, **kw):
            return 0, ""
        with mock.patch.object(routecore, "ROUTES", table), mock.patch.object(main.remote, "dcremote", dcremote), \
                mock.patch.object(main.remote, "this_machine", lambda: "host-b"):
            asyncio.run(b.rebind_far(94))
            self.assertEqual(routecore.load(), {"/s|%0": {"channel": "1", "pid": "20"}})

    def test_every_machine_told_once_per_epoch(self):
        b = self.make()
        calls = []

        async def dcremote(host, *args, **kw):
            calls.append((host,) + args)
            return (255 if host == "host-c" and [c[0] for c in calls].count("host-c") == 1 else 0), ""
        with mock.patch.object(main.remote, "inventory", lambda: {"machines": MACHINES}), \
                mock.patch.object(main.remote, "this_machine", lambda: "host-b"), \
                mock.patch.object(main.remote, "dcremote", dcremote), \
                mock.patch.object(main.config_mod, "write_bot_host", lambda m, e: calls.append(("here", m, e)) or True):
            asyncio.run(b.announce_host())
            asyncio.run(b.announce_host())
            asyncio.run(b.announce_host())
        self.assertEqual(calls.count(("here", "host-b", 4)), 1)
        self.assertEqual(calls.count(("host-a", "bot-host", "host-b", "4")), 1)
        self.assertEqual([c for c in calls if c[0] == "host-c"], [("host-c", "bot-host", "host-b", "4")] * 2,
                         "not reached: tried again, then no more")


class NothingWaitsInARound(unittest.TestCase):
    """#code-review-2, #code-review on 4844bb5 (manager): nothing waits on ssh in a lease round after the read; a
    machine out of reach does not make the next read late (leader_ok() would lapse)."""

    def test_a_machine_stuck_25_seconds(self):
        b = Bot.make(Bot())
        b.primary_ssh_ok, b.back_since = False, None
        b.lease = types.SimpleNamespace(entries=[], now=T0, epoch=4, write_ok=1, other=None, epoch_known=lambda: True,
                                        reads=0, me="host-b")
        b.lease.channel = lambda: object()

        async def round_():
            return "host-b"
        b.lease.round = round_
        b.discord_ok = lambda: None
        b.old_bot = lambda host: None
        stuck = []

        async def dcremote(host, *args, **kw):
            stuck.append(host)
            await asyncio.sleep(25)
            return 255, ""

        async def go():
            import time as _t
            with mock.patch.object(main.remote, "inventory", lambda: {"machines": MACHINES}), \
                    mock.patch.object(main.remote, "this_machine", lambda: "host-b"), \
                    mock.patch.object(main.remote, "dcremote", dcremote), \
                    mock.patch.object(main.remote, "EPOCH", None), mock.patch.object(main.remote, "on_old", None), \
                    mock.patch.object(main.config_mod, "write_bot_host", lambda m, e: True):  # (lease_round sets them)
                times = []
                for _ in range(3):
                    t = _t.monotonic()
                    self.assertFalse(await b.lease_round())
                    times.append(_t.monotonic() - t)
                    await asyncio.sleep(0.05)
                self.assertLess(max(times), 2, "each round back at once")
                self.assertIn("host-a", stuck, "the primary was asked (in the background)")
                self.assertEqual(len([h for h in stuck if h == "host-a"]), 2, "bot-host and info, one task each at a time")
                for t in b.bg_tasks.values():
                    t.cancel()
        asyncio.run(go())

    def test_bot_host_all_at_once_ten_seconds_in_all(self):
        b = Bot.make(Bot())

        async def dcremote(host, *args, **kw):
            await asyncio.sleep(30 if host == "host-a" else 0)
            return 0, ""

        async def go():
            import time as _t
            with mock.patch.object(main.remote, "inventory", lambda: {"machines": MACHINES}), \
                    mock.patch.object(main.remote, "this_machine", lambda: "host-b"), \
                    mock.patch.object(main.remote, "dcremote", dcremote), \
                    mock.patch.object(main.config_mod, "write_bot_host", lambda m, e: True), \
                    mock.patch.object(main.Bot, "TELL_SECS", 0.3):
                b.TELL_SECS = 0.3
                t = _t.monotonic()
                done = await b.tell_hosts("host-b", 4, ["host-a", "host-b", "host-c"])
                self.assertLess(_t.monotonic() - t, 1)
            self.assertEqual(done, {"host-b", "host-c"}, "the one out of time: next round")
        asyncio.run(go())


class LeaseView(unittest.TestCase):
    """C-181: after each good lease read the bot leaves what it saw in state/lease-view.json for the status boards on
    its machine, through the background writer (save_later: never a write in the event loop); a round whose read
    failed leaves the last one as it was, so it grows old and the boards stop."""

    def test_written_after_a_good_read_only_and_off_the_loop(self):
        b = Bot.make(Bot())
        entries = [{"machine": "host-a", "epoch": 4, "leader": True, "lt": T0, "at": T0, "down": None},
                   {"machine": "host-b", "epoch": 3, "leader": False, "lt": None,
                    "at": T0 - datetime.timedelta(seconds=40), "down": None}]
        b.lease = types.SimpleNamespace(entries=entries, now=T0, epoch=4, other=None, epoch_known=lambda: True,
                                        reads=0, me="host-a", channel=lambda: object(), giving_up=lambda: False)
        b.good = True

        async def round_():
            if b.good:
                b.lease.reads += 1
                return "host-a"
            return None
        b.lease.round = round_
        b.leader_ok = lambda: False  # (nothing of the holding side here: only what the round leaves behind)
        b.discord_ok = lambda: None
        b.old_bot = lambda host: None
        saved = []

        async def go():
            with mock.patch.object(main, "save_later", lambda path, obj: saved.append((path, obj))), \
                    mock.patch.object(main, "save_json", side_effect=AssertionError("not in the event loop")), \
                    mock.patch.object(main, "STATE", "/st"), \
                    mock.patch.object(main.remote, "EPOCH", None), mock.patch.object(main.remote, "on_old", None), \
                    mock.patch.object(main.time, "time", lambda: 1234.5):
                self.assertFalse(await b.lease_round())
                b.good = False
                self.assertFalse(await b.lease_round())
        asyncio.run(go())
        self.assertEqual(saved, [("/st/lease-view.json", {
            "at": 1234.5, "machine": "host-a", "holder": "host-a", "leaders": ["host-a"],
            "machines": {"host-a": {"leader": True, "epoch": 4, "age": 0.0},
                         "host-b": {"leader": False, "epoch": 3, "age": 40.0}}})], "the failed read: nothing")

    def stepping_down(self, how, removable=True):
        """give_up() or fenced_out() on a stand-in bot: what was on disk when the leader: false edit went."""
        state = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(state, ignore_errors=True))
        for name in ("heartbeat", "lease-view.json"):
            with open(os.path.join(state, name), "w") as f:
                f.write("1")
        seen = []
        b = types.SimpleNamespace(held_back=[], audits=[])

        async def write(leader):
            seen.append((leader, sorted(os.listdir(state))))
        b.lease = types.SimpleNamespace(write=write)

        async def audit(text):
            b.audits.append(text)

        async def close():
            seen.append(("closed", sorted(os.listdir(state))))
        b.audit, b.close, b.arm_end, b.end_now = audit, close, lambda: None, lambda: None

        async def kept(bot):
            return None
        real_remove = os.remove

        def remove(path):
            if not removable:
                raise PermissionError(path)
            real_remove(path)
        with mock.patch.object(main, "STATE", state), mock.patch.object(main, "say_lane_kept", kept), \
                mock.patch.object(main, "stepped_down_note", lambda bot: ""), \
                mock.patch.object(main.os, "remove", remove):
            if how == "give_up":
                asyncio.run(main.Bot.give_up(b))
            else:
                asyncio.run(main.Bot.fenced_out(b, 9))
        return b, seen

    def test_giving_up_takes_the_boards_marks_away_before_the_leader_false_edit(self):
        """#code-review on 68d35325, manager: the status boards here stop at once as the bot steps down."""
        b, seen = self.stepping_down("give_up")
        self.assertEqual(seen, [(False, []), ("closed", [])])
        self.assertEqual(b.exit_code, 4)

    def test_fenced_out_takes_them_away_too(self):
        b, seen = self.stepping_down("fenced")
        self.assertEqual(seen, [("closed", [])], "no leader: false edit when fenced, as before")

    def test_a_mark_that_cannot_be_taken_away_never_stops_stepping_down(self):
        b, seen = self.stepping_down("give_up", removable=False)
        self.assertEqual([s[0] for s in seen], [False, "closed"])
        self.assertEqual(b.exit_code, 4)

    def test_no_heartbeat_once_stepping_down(self):
        state = tempfile.mkdtemp()
        b = types.SimpleNamespace(lease_over=True, leader_ok=lambda: True, standby_on=False)

        async def once(secs):
            raise asyncio.CancelledError
        with mock.patch.object(main, "STATE", state), mock.patch.object(main.asyncio, "sleep", once):
            with self.assertRaises(asyncio.CancelledError):
                asyncio.run(main.Bot.heartbeat(b))
        self.assertFalse(os.path.exists(os.path.join(state, "heartbeat")), "drop_board_marks() took it away for good")
        b.lease_over = False
        with mock.patch.object(main, "STATE", state), mock.patch.object(main.asyncio, "sleep", once):
            with self.assertRaises(asyncio.CancelledError):
                asyncio.run(main.Bot.heartbeat(b))
        self.assertTrue(os.path.exists(os.path.join(state, "heartbeat")))

    def test_no_lease_channel_leaves_a_no_lease_view(self):
        """manager on 68d35325: with a standby but no lease channel the bot runs as without one; its boards follow
        the heartbeat (lease-view's no_lease, through the background writer)."""
        b = Bot.make(Bot())
        b.lease = types.SimpleNamespace(reads=0, me="host-a", channel=lambda: None)

        async def round_():
            return None
        b.lease.round = round_
        saved = []

        async def go():
            with mock.patch.object(main, "save_later", lambda path, obj: saved.append((path, obj))), \
                    mock.patch.object(main, "STATE", "/st"), mock.patch.object(main.remote, "LEASED", True), \
                    mock.patch.object(main.remote, "inventory", lambda: {"machines": MACHINES}), \
                    mock.patch.object(main.time, "time", lambda: 7.0):
                self.assertTrue(await b.lease_round())
        asyncio.run(go())
        self.assertEqual(b.audits[0], "設定了備援，但找不到 #%s，備援不會接手（在這台跑 altention standby setup host-b auto 會建好，跑完會重啟機器人一次；"
                                      "原本設成只通知的，auto 換成 notify）" % main.config.LEASE_CHANNEL, "a fix that makes it, not doctor")
        self.assertEqual(saved, [("/st/lease-view.json", {"at": 7.0, "machine": "host-a", "holder": None, "leaders": [],
                                                          "machines": {}, "no_lease": main.config.LEASE_CHANNEL})])

    def test_both_holding_both_listed(self):
        e = lambda m, ep: {"machine": m, "epoch": ep, "leader": True, "lt": T0, "at": T0, "down": "load"}  # noqa: E731
        got = main.lease_mod.view([e("host-a", 4), e("host-b", 5)], T0, "host-a", "host-b", 1.0)
        self.assertEqual((got["holder"], got["leaders"]), ("host-b", ["host-a", "host-b"]))
        self.assertEqual(got["machines"]["host-b"]["down"], "load")


class Recovered(unittest.TestCase):
    """The standby took over and went down itself (no failback): the primary takes what it pushed while it was the
    bot, turned as a failback would (#code-review on 8778f8d, manager)."""

    def write(self, meta, channels):
        d = tempfile.mkdtemp()
        with open(os.path.join(d, "meta.json"), "w") as f:
            json.dump(meta, f)
        with open(os.path.join(d, "channels.json"), "w") as f:
            json.dump(channels, f)
        return d

    def test_turned_as_a_failback(self):
        own = os.path.join(tempfile.mkdtemp(), "channels.json")
        with open(own, "w") as f:
            f.write("{}")
        os.utime(own, (1000, 1000))
        pushed = {"11": {"home": {"host": "host-a", "socket": "/a", "session": "dc-t"}, "took_from": {"machine": "host-a", "tmux": "dc-t"}},
                  "21": {"tmux": "dc-new-0021", "session_id": "s21", "cwd": "/w"}}
        d = self.write({"from": "host-b", "epoch": 4, "at": 2000, "bot_socket": "/t/b/dcbot"}, pushed)
        channels, meta = standby.recovered_channels(d, "host-a", 3, own)
        self.assertEqual(channels["11"], {"tmux": "dc-t"})
        self.assertEqual(channels["21"]["home"], {"host": "host-b", "socket": "/t/b/dcbot", "session": "dc-new-0021", "window": ""})
        self.assertIsNone(standby.recovered_channels(d, "host-a", 4, own), "not pushed during a takeover after it")
        self.assertIsNone(standby.recovered_channels(self.write({"from": "host-b", "epoch": 4, "at": 900}, pushed), "host-a", 3, own),
                          "older than this machine's own")
        self.assertIsNone(standby.recovered_channels(self.write({"from": "host-a", "epoch": 4, "at": 2000}, pushed), "host-a", 3, own),
                          "its own push")


class Hosts(unittest.TestCase):
    def test_standbys_and_machines_channels_name(self):
        """#code-review-2 on 8778f8d (manager): events are read from the standbys and from any machine a channel
        names, dcattach or not."""
        from bot import remote
        machines = {"machines": {"host-a": {"roles": ["dcbot"], "ssh": ["a"]}, "host-b": {"roles": ["dcbot-standby"], "ssh": ["b"]},
                                 "host-d": {"roles": [], "ssh": ["d"]}, "host-e": {"roles": [], "ssh": ["e"]}}}
        with mock.patch.object(remote, "inventory", lambda: machines), mock.patch.object(remote, "this_machine", lambda: "host-a"):
            got = remote.hosts({"1": {"home": {"host": "host-d", "socket": "/d"}}, "2": {"tmux": "x"}})
            plain = remote.hosts()
        self.assertEqual(sorted(got), ["host-b", "host-d"])
        self.assertEqual(plain, {}, "the setup's list and posts: dcattach only, as ever (manager on 4844bb5)")


class Writer(unittest.TestCase):
    def test_an_older_epoch_does_not_overwrite(self):
        from altention import config
        home = tempfile.mkdtemp()
        with mock.patch.dict(os.environ, {"HOME": home}):
            self.assertTrue(config.write_bot_host("host-b", 4))
            self.assertFalse(config.write_bot_host("host-a", 3))
            self.assertTrue(config.write_bot_host("host-a", 4), "the same epoch: the failback's")
            self.assertEqual(config.bot_host(), "host-a")


if __name__ == "__main__":
    unittest.main()
