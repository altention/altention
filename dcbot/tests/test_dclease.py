"""C-163 step 2: the lease before the bot starts (tools/dclease gate, lease.decide/still_mine/may_take_over), the
supervisor's use of it, and the bot's fence. No real Discord: a stand-in REST API with a stand-in Discord clock."""
import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))))
from altention import testing as _testing  # noqa: E402
_testing.guard_home()  # tests never touch the real home (altention/testing.py; 11-08: one left a real dcremote-owner)
import asyncio
import contextlib
import datetime
import io
import importlib.machinery
import importlib.util
import json
import os
import tempfile
import time
import unittest
from unittest import mock

from bot import lease
from tests import tmuxguard  # noqa: F401  (no test reaches a real tmux server)

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
T0 = datetime.datetime(2026, 11, 8, 0, 0, tzinfo=datetime.timezone.utc)
BOT_ID = "42"


def load_tool():
    loader = importlib.machinery.SourceFileLoader("dclease", os.path.join(HERE, "tools", "dclease"))
    spec = importlib.util.spec_from_loader("dclease", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


class Api(object):
    """#bot-lease by REST, as Discord answers it; `now` is Discord's clock."""

    def __init__(self):
        self.now, self.msgs, self.posted, self.calls = T0, [], [], 0

    def iso(self):
        return self.now.isoformat()

    def __call__(self, method, path, body=None):
        self.calls += 1
        if path == "/users/@me":
            return {"id": BOT_ID}
        if path.startswith("/guilds/"):
            return [{"id": "100", "name": "bot-lease", "type": 0}, {"id": "200", "name": "notifications", "type": 0}]
        if method == "GET":
            return list(reversed(self.msgs))  # (newest first)
        if method == "POST" and "/channels/200/" in path:
            self.posted.append(body)
            return {}
        if method == "POST":
            m = {"id": str(len(self.msgs) + 1), "author": {"id": BOT_ID}, "content": body["content"],
                 "timestamp": self.iso(), "edited_timestamp": None}
            self.msgs.append(m)
            return m
        mid = path.rsplit("/", 1)[1]
        m = next(x for x in self.msgs if x["id"] == mid)
        m.update(content=body["content"], edited_timestamp=self.iso())
        return m

    def put(self, machine, epoch, leader, lt=None, at=None):
        self.msgs.append({"id": str(len(self.msgs) + 1), "author": {"id": BOT_ID}, "timestamp": (at or self.now).isoformat(),
                          "edited_timestamp": (at or self.now).isoformat(), "content": lease.worded(machine, epoch, leader, lt)})

    def tick(self, secs):
        self.now += datetime.timedelta(seconds=secs)


MACHINES = {"host-a": {"roles": ["dcbot"]}, "host-b": {"roles": [lease.ROLE]}}


class Pure(unittest.TestCase):
    def e(self, m, epoch, leader=True, lt=T0):
        return {"machine": m, "epoch": epoch, "leader": leader, "lt": lt}

    def test_roles(self):
        self.assertEqual(lease.role_of("host-a", MACHINES), "primary")
        self.assertEqual(lease.role_of("host-b", MACHINES), "standby")
        self.assertIsNone(lease.role_of("host-c", MACHINES))

    def test_decide(self):
        stale = T0 - datetime.timedelta(seconds=lease.FRESH_SECS + 1)
        self.assertEqual(lease.decide("primary", "host-a", [self.e("host-b", 2, lt=stale)], T0, []), "start")
        self.assertEqual(lease.decide("primary", "host-a", [self.e("host-b", 2)], T0, []), "wait")
        self.assertEqual(lease.decide("standby", "host-b", [self.e("host-a", 1)], T0, []), "wait")
        self.assertEqual(lease.decide("standby", "host-b", [self.e("host-a", 1, lt=stale)], T0, []), "takeover")
        self.assertEqual(lease.decide("standby", "host-b", [self.e("host-a", 9, leader=False)], T0, []), "takeover",
                         "a leader: false edit never stands in the way (section 1.1)")

    def test_still_mine(self):
        stale = T0 - datetime.timedelta(seconds=lease.FRESH_SECS + 1)
        order = ["host-a", "host-b"]
        self.assertTrue(lease.still_mine([self.e("host-a", 1, lt=stale), self.e("host-b", 2)], T0, "host-b", 2, "host-a", order))
        self.assertFalse(lease.still_mine([self.e("host-a", 1), self.e("host-b", 2)], T0, "host-b", 2, "host-a", order),
                         "the primary fresh again: given up, whatever the epochs (fifth version)")
        self.assertFalse(lease.still_mine([self.e("host-a", 1, lt=stale), self.e("host-b", 1)], T0, "host-b", 2, "host-a", order),
                         "the edit did not take (still the old epoch)")

    def test_new_epoch(self):
        self.assertEqual(lease.new_epoch([self.e("host-a", 1), self.e("host-b", 4)], 2), 5)
        self.assertEqual(lease.new_epoch([], 3), 4, "its own counts too")

    def test_new_epochs_never_meet(self):
        """#code-review on 6f7327b (manager's (a)): epoch mod N is the machine's place among the members by name."""
        machines = {"host-b": {"roles": [lease.ROLE]}, "host-a": {"roles": ["dcbot"]}, "host-c": {"roles": ["chatlog"]}}
        self.assertEqual(lease.members(machines), ["host-b", "host-a"], "the standbys by name, the primary last; no part, no place")
        seen = [self.e("host-a", 1), self.e("host-b", 1)]
        a, b = lease.new_epoch(seen, 1, "host-a", machines), lease.new_epoch(seen, 1, "host-b", machines)
        self.assertEqual((a, b), (3, 2), "from the same epochs the primary gets the bigger one")
        self.assertEqual((a % 2, b % 2), (1, 0))

    def test_limits(self):
        self.assertIsNone(lease.may_take_over([], 10 ** 6))
        self.assertEqual(lease.may_take_over([10 ** 6 - 60], 10 ** 6), "calm")
        self.assertEqual(lease.may_take_over([10 ** 6 - 7200 * i for i in (1, 2, 3)], 10 ** 6), "day")


class Gate(unittest.TestCase):
    def setUp(self):
        self.tool = load_tool()
        self.state = tempfile.mkdtemp()
        for name, value in (("TAKEOVERS", "lease-takeovers.json"), ("TOOK_OVER", "took-over.json"), ("FENCED", "fenced"),
                            ("NOTE", "lease-gate-note.json"), ("ROLE_FILE", "lease-role"),
                            ("FAILBACK_TAKEN", "failback-taken.json"), ("FAILED_BACK", "failed-back.json"),
                            ("RECOVERED", "standby-recovered.json"), ("REPLAY", "replay-wanted.json"),
                            ("MISSING", "lease-primary-missing.json"), ("DOWN", "lease-down.json")):
            p = mock.patch.object(self.tool, name, os.path.join(self.state, value))
            p.start()
            self.addCleanup(p.stop)
        for p in (mock.patch.object(self.tool.config, "GUILD_ID", 1), mock.patch.object(self.tool.config, "LEASE_CHANNEL", "bot-lease"),
                  mock.patch.object(self.tool.config, "OWNER_ID", 7)):
            p.start()
            self.addCleanup(p.stop)
        self.api = Api()
        self.fenced = []
        from bot import standby  # (what the primary pushed: meta.json as this engine writes it, so 3.4's checks pass)
        self.standby_dir = os.path.join(self.state, "state-standby")
        os.makedirs(self.standby_dir)
        with open(os.path.join(self.standby_dir, "meta.json"), "w") as f:
            json.dump(standby.meta(self.tool.config.ROOT, "host-a"), f)
        with open(os.path.join(self.standby_dir, "channels.json"), "w") as f:
            json.dump({"1": {"tmux": "dc-a-1"}}, f)
        self.target_state = os.path.join(self.state, "s")
        os.makedirs(self.target_state)
        for name, value in (("STANDBY_DIR", self.standby_dir),):
            p = mock.patch.object(self.tool, name, value)
            p.start()
            self.addCleanup(p.stop)
        p = mock.patch.object(self.tool.config, "STATE", self.target_state)
        p.start()
        self.addCleanup(p.stop)

    def gate(self, me, machines=MACHINES, veto=(False, "ssh 不通"), on_sleep=None, crashing=False, check=None):
        def sleep(secs):
            self.api.tick(secs)
            if on_sleep:
                on_sleep()
        with mock.patch.object(self.tool.remote, "inventory", lambda: {"machines": machines}), \
                mock.patch.object(self.tool.remote, "this_machine", lambda: me), \
                mock.patch.object(self.tool.remote, "alt", lambda: mock.Mock(setting=lambda k, d=None: d)):
            return self.tool.gate(self.tool.Discord(self.api), sleep=sleep, veto=lambda p: veto,
                                  fence_=lambda p, e: self.fenced.append((p, e)) or True, crashing=crashing,
                                  check=check or (lambda: self.fail("no load check expected here")))

    def own(self, machine):
        return next(lease.parse(m["content"]) for m in self.api.msgs if lease.parse(m["content"])["machine"] == machine)

    def test_without_a_standby_nothing_is_asked(self):
        with mock.patch.object(self.tool.remote, "inventory", lambda: {"machines": {"host-a": {"roles": ["dcbot"]}}}):
            self.assertEqual(self.tool.gate(d=mock.Mock(side_effect=AssertionError)), self.tool.START)

    def test_the_primary_starts_when_nobody_holds_it(self):
        self.assertEqual(self.gate("host-a"), self.tool.START)
        self.assertFalse(self.own("host-a")["leader"], "alive only: the bot writes leader true once connected")

    def test_the_primary_waits_while_another_holds_it(self):
        self.api.put("host-b", 3, True)
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(self.gate("host-a"), self.tool.WAIT)
        self.assertIn("dclease: waiting: host-b holds the place", err.getvalue(), "why, for the supervisor's log")

    def crashing_primary(self, lapsed, standby_edited, check=lambda: ""):
        """host-a's last leader: true `lapsed` s ago, host-b's alive edit `standby_edited` s ago; host-a's bot crashing
        (`check`: its load check, passing by default)."""
        lt = self.api.now
        self.api.put("host-a", 1, True, at=lt)
        self.api.tick(lapsed)
        self.api.put("host-b", 2, False, at=self.api.now - datetime.timedelta(seconds=standby_edited))
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            got = self.gate("host-a", crashing=True, check=check)
        return got, err.getvalue()

    def test_marked_down_while_the_standby_holds_the_place(self):
        """Drill item 20, the rig on d8f431c (18:54): the standby took over while the primary's gate only ever said
        "host-b holds it"; the mark came once nobody held it, after the standby had failed back. Now every crashing
        gate decides it (a load check at most each DOWN_CHECK_SECS), whoever holds the place."""
        self.api.put("host-a", 1, False, at=self.api.now - datetime.timedelta(seconds=lease.FRESH_SECS + 60))
        self.api.put("host-b", 2, True)
        checks = []

        def failing():
            checks.append(1)
            return "load check failed: PermissionError"
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(self.gate("host-a", crashing=True, check=failing), self.tool.WAIT)
        self.assertEqual((self.own("host-a")["down"], len(checks)), ("crashing", 1), "main.py unreadable: down")
        self.assertIn("marked down", err.getvalue())
        self.api.tick(60)
        with contextlib.redirect_stderr(io.StringIO()):
            self.gate("host-a", crashing=True, check=failing)
        self.assertEqual((self.own("host-a")["down"], len(checks)), ("crashing", 1), "checked just now: kept, no check")
        mark = self.tool.load(self.tool.DOWN, None)
        mark["checked"] -= lease.DOWN_CHECK_SECS
        self.tool.save(self.tool.DOWN, mark)
        with contextlib.redirect_stderr(io.StringIO()):
            self.gate("host-a", crashing=True, check=lambda: "")
        self.assertIsNone(self.own("host-a")["down"], "fixed: within DOWN_CHECK_SECS no longer down, a failback may come")
        self.assertIs(self.tool.load(self.tool.DOWN, {}).get("down"), False)
        with contextlib.redirect_stderr(io.StringIO()):
            self.gate("host-a", crashing=True, check=lambda: self.fail("checked just now"))
        self.assertIsNone(self.own("host-a")["down"], "not marked again before the next check")

    def test_the_old_file_without_down_counts_as_down(self):
        """#code-review-2 on 47566d3: d8f431c's lease-down.json had no "down" (there = down); read within
        DOWN_CHECK_SECS of its check, still down, not "not down" until the next check."""
        self.tool.save(self.tool.DOWN, {"since": time.time(), "checked": time.time()})
        self.api.put("host-b", 2, True)
        with contextlib.redirect_stderr(io.StringIO()):
            self.gate("host-a", crashing=True, check=lambda: self.fail("checked just now"))
        self.assertEqual(self.own("host-a")["down"], "crashing")

    def test_no_mark_when_not_crashing(self):
        self.tool.save(self.tool.DOWN, {"since": time.time(), "checked": time.time(), "down": True})
        self.api.put("host-b", 2, True)
        with contextlib.redirect_stderr(io.StringIO()):
            self.gate("host-a")
        self.assertIsNone(self.own("host-a")["down"])
        self.assertFalse(os.path.exists(self.tool.DOWN), "the bot stable again (supervisor: no --crashing): gone")

    def test_a_down_mark_goes_with_the_new_epoch_of_a_start(self):
        """Past LEAVE_SECS the crashing primary starts again with a new epoch: still marked down."""
        self.api.put("host-a", 1, False, at=self.api.now - datetime.timedelta(seconds=lease.LEAVE_SECS + 60))
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(self.gate("host-a", crashing=True, check=lambda: "load check failed"), self.tool.START)
        self.assertEqual((self.own("host-a")["epoch"] > 1, self.own("host-a")["down"]), (True, "crashing"))

    def test_left_to_the_standby_and_marked_down(self):
        got, err = self.crashing_primary(lease.FRESH_SECS + 60, 20, check=lambda: "load check failed")
        self.assertEqual(got, self.tool.WAIT)
        self.assertEqual(self.own("host-a")["down"], "crashing")
        self.assertIn("left to the standby", err)

    def test_the_load_check_imports_the_bot(self):
        """The real check: bot.main imported in a child; an unreadable main.py (the drill's item 20) fails it."""
        self.assertEqual(self.tool.load_check(), "")
        with mock.patch.object(self.tool.subprocess, "run", lambda *a, **k: mock.Mock(
                returncode=1, stdout=b"PermissionError: [Errno 13] Permission denied: 'bot/main.py'")):
            self.assertIn("PermissionError", self.tool.load_check())

    def test_a_crashing_primary_leaves_a_lapsed_place_to_a_standby_that_is_up(self):
        """Drill item 17 (manager 11-09): no new epoch at each start, so the standby takes over."""
        got, err = self.crashing_primary(lease.FRESH_SECS + 60, 20)
        self.assertEqual(got, self.tool.WAIT)
        self.assertIn("left to the standby", err)
        self.assertEqual(self.own("host-a")["epoch"], 1, "no new epoch written")
        self.assertEqual(self.gate("host-b"), self.tool.START, "and the standby takes it")

    def test_a_crashing_primary_starts_when_no_standby_is_up(self):
        got, _ = self.crashing_primary(lease.FRESH_SECS + lease.ALIVE_SECS + 60, lease.ALIVE_SECS + 30)
        self.assertEqual(got, self.tool.START)
        self.assertGreater(self.own("host-a")["epoch"], 2, "a new epoch, as before")

    def test_a_crashing_primary_starts_once_the_standby_had_its_time(self):
        got, _ = self.crashing_primary(lease.LEAVE_SECS + 10, 20)
        self.assertEqual(got, self.tool.START, "never nobody being the bot for good")

    def test_a_primary_not_crashing_starts_on_a_lapsed_place(self):
        self.api.put("host-a", 1, True)
        self.api.tick(lease.FRESH_SECS + 10)
        self.api.put("host-b", 2, False)
        self.assertEqual(self.gate("host-a"), self.tool.START)

    def test_a_fence_while_the_other_holds_it_and_gone_once_it_does_not(self):
        with open(os.path.join(self.state, "fenced"), "w") as f:
            f.write("3")
        self.api.put("host-b", 3, True)
        self.assertEqual(self.gate("host-a"), self.tool.WAIT)
        self.api.tick(lease.FRESH_SECS + 60)
        self.assertEqual(self.gate("host-a"), self.tool.START)
        self.assertFalse(os.path.exists(os.path.join(self.state, "fenced")))

    def test_the_standby_waits_while_the_primary_holds_it(self):
        self.api.put("host-a", 1, True)
        self.assertEqual(self.gate("host-b"), self.tool.WAIT)
        self.assertFalse(self.fenced)

    def test_the_standby_takes_over_a_stale_primary(self):
        self.api.put("host-a", 1, True)
        self.api.tick(lease.FRESH_SECS + 60)
        self.assertEqual(self.gate("host-b"), self.tool.START)
        own = self.own("host-b")
        self.assertEqual((own["epoch"], own["leader"]), (2, True), "bigger than 1, and even: host-b's place of two")
        self.assertEqual(self.fenced, [("host-a", 2)])
        with open(os.path.join(self.state, "took-over.json")) as f:
            self.assertEqual(json.load(f)["epoch"], 2)
        with open(os.path.join(self.target_state, "channels.json")) as f:
            self.assertEqual(json.load(f), {"1": {"tmux": "dc-a-1"}}, "the bot's channels taken in (section 3.1)")
        with open(os.path.join(self.target_state, "takeover-base.json")) as f:
            self.assertEqual(json.load(f), {"1": {"tmux": "dc-a-1"}}, "and kept as they were, for the failback")

    def test_no_takeover_on_another_protocol(self):
        """Section 3.4 (manager 11-08): another takeover protocol: notify only, say which (once an hour)."""
        from bot import standby
        with open(os.path.join(self.standby_dir, "meta.json"), "w") as f:
            json.dump(dict(standby.meta(self.tool.config.ROOT, "host-a"), protocol=standby.PROTOCOL + 1), f)
        self.api.put("host-a", 1, True)
        self.api.tick(lease.FRESH_SECS + 60)
        self.assertEqual(self.gate("host-b"), self.tool.WAIT)
        self.assertEqual(self.gate("host-b"), self.tool.WAIT)
        self.assertEqual(len(self.api.posted), 1)
        self.assertIn("沒辦法接手：兩台的引擎差太多，沒辦法互相接手", self.api.posted[0]["content"])
        self.assertEqual(self.own("host-b")["leader"], False, "nothing taken")

    def test_another_engine_commit_takes_over_and_says_both(self):
        from bot import standby
        with open(os.path.join(self.standby_dir, "meta.json"), "w") as f:
            json.dump(dict(standby.meta(self.tool.config.ROOT, "host-a"), engine="a" * 40), f)
        self.api.put("host-a", 1, True)
        self.api.tick(lease.FRESH_SECS + 60)
        with mock.patch.object(standby, "engine_version", lambda root: "b" * 40), \
                mock.patch.object(standby, "release_name", lambda root: "v9.9.9"):
            self.assertEqual(self.gate("host-b"), self.tool.START)
        with open(os.path.join(self.state, "took-over.json")) as f:
            self.assertEqual(json.load(f)["engines"], {"theirs": "aaaaaaa", "mine": "v9.9.9"},
                             "a release by its name, else 7 characters of the commit (#release)")

    def test_trouble_taking_the_state_in_goes_back_to_alive(self):
        """163b's rule (#code-review on 9d2b7bd, manager): anything failing after the leader: true edit."""
        from bot import standby
        self.api.put("host-a", 1, True)
        self.api.tick(lease.FRESH_SECS + 60)
        with mock.patch.object(standby, "take_in", side_effect=OSError("disk full")):
            self.assertNotEqual(self.gate("host-b"), self.tool.START)
        self.assertEqual(self.own("host-b")["leader"], False)
        self.assertFalse(os.path.exists(os.path.join(self.state, "took-over.json")))
        self.assertEqual(self.tool.load(self.tool.TAKEOVERS, []), [], "no takeover took place: not counted (#code-review)")

    def test_a_primary_that_never_joined_is_not_called_down(self):
        """#code-review, #code-review-2 on 3a4e6b1 (manager): a standby just set up, the primary's bot restarting and
        not in #bot-lease yet: the standby waits, takes nothing over and tells nobody."""
        self.api.tick(lease.FRESH_SECS + 60)
        self.assertEqual(self.gate("host-b"), self.tool.WAIT)
        self.assertEqual(self.api.posted, [], "no ⛔ said")
        self.assertFalse(self.own("host-b")["leader"])
        self.assertEqual(self.tool.load(self.tool.TAKEOVERS, []), [], "nothing counted")
        self.assertIsInstance(self.tool.load(self.tool.MISSING, None), float, "since when: for standby-watch and doctor")
        self.api.put("host-a", 1, True)  # the primary's bot up and holding the place: the standby's gate says wait
        self.assertEqual(self.gate("host-b"), self.tool.WAIT)
        self.assertIsNone(self.tool.load(self.tool.MISSING, None),
                          "the primary holds the place: gone again, though the gate returned early (#code-review on e2e60ab)")

    def test_failing_back(self):
        """Section 5: the standby failed back: it does not take over while the primary starts; the primary takes the
        pushed channels in once, as it becomes the bot."""
        from bot import standby
        with open(os.path.join(self.standby_dir, "meta.json"), "w") as f:
            json.dump(dict(standby.meta(self.tool.config.ROOT, "host-b"), failback=True, at=100.0), f)
        with open(os.path.join(self.standby_dir, "channels.json"), "w") as f:
            json.dump({"1": {"home": {"host": "host-b", "session": "dc-x"}}}, f)
        self.tool.save(self.tool.FAILED_BACK, {"primary": "host-a", "at": time.time()})
        self.api.put("host-b", 2, False)
        self.api.tick(lease.FRESH_SECS + 60)
        self.assertEqual(self.gate("host-b"), self.tool.WAIT, "failed back a moment ago: no takeover")
        self.assertEqual(self.gate("host-a"), self.tool.START)
        with open(os.path.join(self.target_state, "channels.json")) as f:
            self.assertEqual(json.load(f), {"1": {"home": {"host": "host-b", "session": "dc-x"}}}, "the standby's word")
        self.assertEqual(self.tool.load(self.tool.FAILBACK_TAKEN, 0), 100.0)
        self.assertEqual(self.tool.load(self.tool.REPLAY, {}).get("from"), "host-b",
                         "the bot takes the questions in from host-b and looks for replies left unposted")
        with open(os.path.join(self.target_state, "channels.json"), "w") as f:
            json.dump({"1": {"later": True}}, f)
        self.api.put("host-a", self.own("host-a")["epoch"], False)
        self.api.tick(lease.FRESH_SECS + 60)
        self.assertEqual(self.gate("host-a"), self.tool.START)
        with open(os.path.join(self.target_state, "channels.json")) as f:
            self.assertEqual(json.load(f), {"1": {"later": True}}, "taken in once only")

    def test_failing_back_channel_by_channel(self):
        """Manager 11-09 (b): what the standby left untouched keeps the primary's own copy (newer than its last push:
        a pane attached again); what it changed, opened or removed is the standby's."""
        from bot import standby
        with open(os.path.join(self.target_state, "channels.json"), "w") as f:
            json.dump({"1": {"pane": {"id": "%0", "pid": 222}}, "2": {"tmux": "dc-2"}, "3": {"tmux": "dc-3"},
                       "5": {"tmux": "dc-5-new"}}, f)
        marks = {"untouched": ["1", "2"], "gone": ["3"]}
        with open(os.path.join(self.standby_dir, "meta.json"), "w") as f:
            json.dump(dict(standby.meta(self.tool.config.ROOT, "host-b"), failback=True, at=100.0, **marks), f)
        with open(os.path.join(self.standby_dir, "channels.json"), "w") as f:
            json.dump({"1": {"pane": {"id": "%0", "pid": 111}}, "2": {"tmux": "dc-2", "session_id": "s"},
                       "4": {"home": {"host": "host-b", "session": "dc-4"}}}, f)
        self.api.put("host-b", 2, False)
        self.api.tick(lease.FRESH_SECS + 60)
        self.assertEqual(self.gate("host-a"), self.tool.START)
        with open(os.path.join(self.target_state, "channels.json")) as f:
            got = json.load(f)
        self.assertEqual(got, {"1": {"pane": {"id": "%0", "pid": 222}}, "2": {"tmux": "dc-2"},
                               "4": {"home": {"host": "host-b", "session": "dc-4"}}, "5": {"tmux": "dc-5-new"}}, got)

    def test_failing_back_with_a_pushed_file_unreadable_writes_no_null(self):
        """#code-review on 67ba1d8: the merge given nothing readable leaves the file as take_in() put it."""
        from bot import standby
        with open(os.path.join(self.target_state, "channels.json"), "w") as f:
            json.dump({"1": {"tmux": "dc-1"}}, f)
        with open(os.path.join(self.standby_dir, "meta.json"), "w") as f:
            json.dump(dict(standby.meta(self.tool.config.ROOT, "host-b"), failback=True, at=100.0, untouched=["1"], gone=[]), f)
        with open(os.path.join(self.standby_dir, "channels.json"), "w") as f:
            f.write("{")
        self.api.put("host-b", 2, False)
        self.api.tick(lease.FRESH_SECS + 60)
        self.assertEqual(self.gate("host-a"), self.tool.START)
        with open(os.path.join(self.target_state, "channels.json")) as f:
            self.assertEqual(f.read(), "{", "not null")

    def test_the_standby_went_down_too(self):
        """#code-review on 8778f8d (manager): the standby took over and went down without failing back: the primary,
        becoming the bot, takes what it pushed while it was the bot, turned as a failback would, and says so."""
        from bot import standby
        with open(os.path.join(self.standby_dir, "meta.json"), "w") as f:
            json.dump(dict(standby.meta(self.tool.config.ROOT, "host-b"), epoch=2, at=time.time(), bot_socket="/t/b/dcbot"), f)
        with open(os.path.join(self.standby_dir, "channels.json"), "w") as f:
            json.dump({"21": {"tmux": "dc-new-0021", "session_id": "s21", "cwd": "/w"}}, f)
        self.api.put("host-a", 1, False)
        self.api.put("host-b", 2, True)
        self.api.tick(lease.FRESH_SECS + 60)
        self.assertEqual(self.gate("host-a"), self.tool.START)
        with open(os.path.join(self.target_state, "channels.json")) as f:
            self.assertEqual(json.load(f)["21"]["home"], {"host": "host-b", "socket": "/t/b/dcbot", "session": "dc-new-0021", "window": ""})
        self.assertEqual(self.tool.load(self.tool.RECOVERED, {})["standby"], "host-b")

    def test_the_time_limit_counts(self):
        """Cut short by the time limit (like a kill): counted all the same (manager 11-08)."""
        from bot import standby
        self.api.put("host-a", 1, True)
        self.api.tick(lease.FRESH_SECS + 60)
        with mock.patch.object(standby, "take_in", side_effect=self.tool.Budget()):
            try:
                self.gate("host-b")
            except self.tool.Budget:
                pass
        self.assertEqual(self.own("host-b")["leader"], False)
        self.assertEqual(len(self.tool.load(self.tool.TAKEOVERS, [])), 1)

    def test_ssh_shows_the_primary_alive(self):
        self.api.put("host-a", 1, True)
        self.api.tick(lease.FRESH_SECS + 60)
        self.assertEqual(self.gate("host-b", veto=(True, "ssh 通，心跳 10 秒前")), self.tool.WAIT)
        self.assertEqual(self.own("host-b")["epoch"], 0, "nothing taken")

    def test_the_primary_back_during_the_wait_gives_it_up(self):
        """Section 1.2, fifth version: the read 15 s on checks the premise again."""
        self.api.put("host-a", 1, True)
        self.api.tick(lease.FRESH_SECS + 60)

        def primary_back():
            m = self.api.msgs[0]
            m.update(content=lease.worded("host-a", 1, True, None), edited_timestamp=self.api.iso())
        self.assertEqual(self.gate("host-b", on_sleep=primary_back), self.tool.WAIT)
        self.assertFalse(self.own("host-b")["leader"], "back to alive only")
        self.assertFalse(self.fenced)

    def test_not_twice_in_half_an_hour_and_three_a_day_said_once(self):
        import time
        with open(os.path.join(self.state, "lease-takeovers.json"), "w") as f:
            json.dump([time.time() - 60], f)
        self.api.put("host-a", 1, True)
        self.api.tick(lease.FRESH_SECS + 60)
        self.assertEqual(self.gate("host-b"), self.tool.WAIT)
        self.assertEqual(self.api.posted, [])
        with open(os.path.join(self.state, "lease-takeovers.json"), "w") as f:
            json.dump([time.time() - 7200 * i for i in (1, 2, 3)], f)
        self.assertEqual(self.gate("host-b"), self.tool.WAIT)
        self.assertEqual(self.gate("host-b"), self.tool.WAIT)
        self.assertEqual(len(self.api.posted), 1, "said once")
        self.assertIn("<@7>", self.api.posted[0]["content"])

    def broken(self, me, error):
        broken = mock.Mock()
        broken.channel = "100"
        broken.entries.side_effect = error
        with mock.patch.object(self.tool.remote, "inventory", lambda: {"machines": MACHINES}), \
                mock.patch.object(self.tool.remote, "this_machine", lambda: me):
            return self.tool.gate(broken)

    def test_trouble_on_a_standby_is_wait(self):
        import urllib.error
        for error in (OSError("503"), urllib.error.HTTPError("u", 403, "no", {}, None), ValueError("bug")):
            self.assertEqual(self.broken("host-b", error), self.tool.WAIT, error)
        with open(os.path.join(self.state, "lease-role")) as f:
            self.assertEqual(f.read(), "standby", "said for the supervisor")

    def test_trouble_on_the_primary_by_kind(self):
        """manager 11-08: Discord out of reach waits (each minute again), a setup trouble starts; a fence waits."""
        import urllib.error
        self.assertEqual(self.broken("host-a", urllib.error.URLError("down")), self.tool.WAIT)
        self.assertEqual(self.broken("host-a", urllib.error.HTTPError("u", 429, "slow", {}, None)), self.tool.WAIT)
        self.assertEqual(self.broken("host-a", urllib.error.HTTPError("u", 503, "x", {}, None)), self.tool.WAIT)
        with open(os.path.join(self.state, "lease-gate-note.json")) as f:
            self.assertEqual(json.load(f)["kind"], "connection")
        self.assertEqual(self.broken("host-a", urllib.error.HTTPError("u", 403, "no", {}, None)), self.tool.START)
        with open(os.path.join(self.state, "lease-gate-note.json")) as f:
            got = json.load(f)
        self.assertEqual((got["kind"], got["why"]), ("setup", "沒有權限"), "said by the bot once up, in plain words")
        with open(os.path.join(self.state, "fenced"), "w") as f:
            f.write("3")
        os.remove(os.path.join(self.state, "lease-gate-note.json"))
        self.assertEqual(self.broken("host-a", urllib.error.HTTPError("u", 403, "no", {}, None)), self.tool.WAIT,
                         "fenced: never started on a guess (#code-review on ccd8baa)")
        self.assertFalse(os.path.exists(os.path.join(self.state, "lease-gate-note.json")),
                         "no note saying it started anyway (#code-review on 3757c8c)")

    def test_a_message_gone_is_not_a_channel_gone(self):
        import io
        import urllib.error
        gone = urllib.error.HTTPError("u", 404, "x", {}, io.BytesIO(b'{"code": 10008, "message": "Unknown Message"}'))
        self.assertEqual(self.tool.plain(gone), "這台自己的那則訊息不在了")
        gone = urllib.error.HTTPError("u", 404, "x", {}, io.BytesIO(b'{"code": 10003}'))
        self.assertEqual(self.tool.plain(gone), "頻道不在")

    def test_a_standby_holding_it_restarts_without_a_takeover(self):
        """#code-review on ccd8baa: its bot restarted (a deploy, a crash) while it holds the place."""
        self.api.put("host-a", 1, True)
        self.api.tick(lease.FRESH_SECS + 60)
        self.assertEqual(self.gate("host-b"), self.tool.START)
        self.api.tick(30)
        m = next(x for x in self.api.msgs if lease.parse(x["content"])["machine"] == "host-b")
        m.update(content=lease.worded("host-b", 2, True, None), edited_timestamp=self.api.iso())  # its bot's round
        self.api.tick(60)
        self.assertEqual(self.gate("host-b"), self.tool.START, "not 30 minutes of nothing")
        self.assertTrue(self.own("host-b")["leader"], "no leader: false edit")
        with open(os.path.join(self.state, "lease-takeovers.json")) as f:
            self.assertEqual(len(json.load(f)), 1, "not counted again")

    def test_the_primary_starting_during_the_standbys_ssh_check(self):
        """#code-review on 6f7327b: the primary's gate runs while the standby waits on ssh; both work out a new epoch
        from what they read; they differ, and only one of them holds the place."""
        self.api.put("host-a", 1, False)
        self.api.put("host-b", 1, False)
        self.api.tick(lease.FRESH_SECS + 60)
        primary = {}

        def veto_running_the_primary(p):
            primary["got"] = self.gate("host-a")
            return False, "ssh 不通"
        with mock.patch.object(self.tool.remote, "inventory", lambda: {"machines": MACHINES}), \
                mock.patch.object(self.tool.remote, "this_machine", lambda: "host-b"), \
                mock.patch.object(self.tool.remote, "alt", lambda: mock.Mock(setting=lambda k, d=None: d)):
            standby = self.tool.gate(self.tool.Discord(self.api), sleep=self.api.tick,
                                     veto=veto_running_the_primary, fence_=lambda p, e: True)
        a, b = self.own("host-a")["epoch"], self.own("host-b")["epoch"]
        self.assertNotEqual(a, b, "never the same epoch")
        # Both read the same epochs; the primary, last among the members, gets the bigger new one (3 against 2): the
        # standby's read 15 s on sees it and gives up; the primary goes on (manager 11-08).
        self.assertEqual((a, b, primary["got"], standby), (3, 2, self.tool.START, self.tool.WAIT))
        self.assertFalse(self.own("host-b")["leader"], "back to leader: false")

    def test_a_same_or_bigger_epoch_written_meanwhile_makes_the_standby_give_up(self):
        """still_mine (#code-review-2 on 6f7327b, manager): another machine's epoch as big or bigger, leader or not,
        on the read 15 s on: the standby gives up (here the primary's new epoch, written between the two reads)."""
        self.api.put("host-a", 1, False)
        self.api.tick(lease.FRESH_SECS + 60)

        def primary_writes_a_bigger_epoch(secs):
            m = self.api.msgs[0]
            m.update(content=lease.worded("host-a", 4, False, None), edited_timestamp=self.api.iso())
            self.api.tick(secs)
        self.assertEqual(self.gate("host-b", on_sleep=lambda: primary_writes_a_bigger_epoch(0)), self.tool.WAIT)
        self.assertFalse(self.own("host-b")["leader"], "back to leader: false")

    def test_the_primary_after_the_standbys_takeover_edit_waits(self):
        """The other order: the primary's gate reads after the standby wrote leader: true: it waits."""
        self.api.put("host-a", 1, False)
        self.api.put("host-b", 1, False)
        self.api.tick(lease.FRESH_SECS + 60)
        primary = {}

        def sleep_running_the_primary(secs):
            self.api.tick(1)
            primary["got"] = self.gate("host-a")
            self.api.tick(secs)
        with mock.patch.object(self.tool.remote, "inventory", lambda: {"machines": MACHINES}), \
                mock.patch.object(self.tool.remote, "this_machine", lambda: "host-b"), \
                mock.patch.object(self.tool.remote, "alt", lambda: mock.Mock(setting=lambda k, d=None: d)):
            standby = self.tool.gate(self.tool.Discord(self.api), sleep=sleep_running_the_primary,
                                     veto=lambda p: (False, "ssh 不通"), fence_=lambda p, e: True)
        self.assertEqual((primary["got"], standby), (self.tool.WAIT, self.tool.START))

    def test_trouble_after_the_takeover_edit_puts_it_back(self):
        """#code-review-2 on ccd8baa: no fresh leader: true left behind (it would be the bot unchecked next time)."""
        self.api.put("host-a", 1, True)
        self.api.tick(lease.FRESH_SECS + 60)
        real = self.tool.Discord.entries
        calls = []

        def entries(d):
            calls.append(1)
            if len(calls) == 4:  # (the read after the 15 s)
                raise OSError("503")
            return real(d)
        with mock.patch.object(self.tool.Discord, "entries", entries):
            self.assertEqual(self.gate("host-b"), self.tool.WAIT)
        self.assertFalse(self.own("host-b")["leader"])

    def test_ssh_reached_but_no_state(self):
        with mock.patch.object(self.tool, "ssh", lambda h, c: (0, "NOSTATE\n")), \
                mock.patch.object(self.tool.remote, "engine_of", lambda h: "/e"):
            blocked, said = self.tool.ssh_veto("host-a")
        self.assertFalse(blocked)
        self.assertIn("沒查到", said)

    def test_etime(self):
        self.assertEqual([self.tool.elapsed(x) for x in ("0", "05", "01:05", "02:01:05", "3-02:01:05")],
                         [0, 5, 65, 7265, 3 * 86400 + 7265])


class Supervisor(unittest.TestCase):
    def gate(self, role, result, stderr=b"", crashing=False, logged=None, argv=None):
        import subprocess
        import supervisor

        def run(cmd, **k):
            if argv is not None:
                argv.append(cmd)
            if isinstance(result, Exception):
                raise result
            return subprocess.CompletedProcess(cmd, result, b"", stderr)
        with mock.patch.object(supervisor.subprocess, "run", run), mock.patch.object(supervisor, "PY", __file__), \
                mock.patch.object(supervisor, "log", (logged.append if logged is not None else lambda *a: None)), \
                mock.patch.object(supervisor, "read", lambda name: role):
            return supervisor.lease_gate(crashing=crashing)

    def test_crashing_is_said_to_the_gate(self):
        argv = []
        self.gate("primary", 0, crashing=True, argv=argv)
        self.gate("primary", 0, argv=argv)
        self.assertEqual([c[2:] for c in argv], [["gate", "--crashing"], ["gate"]])

    def test_the_bot_is_told_it_is_crashing(self):
        """Manager 11-10: only a restart while crashing waits STABLE_SECS; the supervisor says so to the bot, which
        takes it out of the environment."""
        with open(os.path.join(HERE, "supervisor.py"), encoding="utf-8") as f:
            src = f.read()
        self.assertIn('env = {k: v for k, v in os.environ.items() if k != "DCBOT_CRASHING"}', src)
        self.assertIn('if crashes:', src[src.index('env = {k: v'):src.index('proc = subprocess.Popen([PY, "-m", "bot.main"]')])
        self.assertIn('cwd=ROOT, env=env)', src)
        from bot import main
        env = {"DCBOT_CRASHING": "1", "HOME": "/h"}
        self.assertTrue(main.crashing_from_env(env))
        self.assertEqual(env, {"HOME": "/h"}, "nothing the bot starts carries it")
        self.assertFalse(main.crashing_from_env(env))

    def test_why_it_waits_is_logged_once_a_change(self):
        """Drill problem B (manager 11-09): the gate's stderr with its 10, logged; the same words with other times
        are not logged again each minute."""
        import supervisor
        logged = []
        self.addCleanup(setattr, supervisor, "GATE_SAID", None)
        supervisor.GATE_SAID = None
        one = b"dclease: waiting: host-b holds the place (read at 2026-11-08 00:01:00.123000+00:00): x"
        two = b"dclease: waiting: host-b holds the place (read at 2026-11-08 00:02:00.456000+00:00): x"
        for err in (one, two, b"", b"dclease: waiting: fenced: epoch 3 here"):
            self.assertEqual(self.gate("primary", 10, stderr=err, logged=logged), "wait")
        self.assertEqual(len(logged), 2, logged)
        self.assertIn("host-b holds the place", logged[0])
        self.assertIn("fenced", logged[1])
        self.gate("primary", 0, logged=logged)
        self.gate("primary", 10, stderr=one, logged=logged)
        self.assertEqual(len(logged), 3, "said again after a start")

    def test_lease_gate_on_the_primary(self):
        import subprocess
        for result, want in ((0, "start"), (10, "wait"), (1, "start"), (subprocess.TimeoutExpired("x", 1), "start")):
            self.assertEqual(self.gate("primary", result), want, result)

    def test_lease_gate_on_a_standby_starts_only_on_a_clear_yes(self):
        """#code-review-2 on ccd8baa: a gate killed by the time limit, or broken, is "wait" there."""
        import subprocess
        for result, want in ((0, "start"), (10, "wait"), (1, "wait"), (subprocess.TimeoutExpired("x", 1), "wait")):
            self.assertEqual(self.gate("standby", result), want, result)

    def test_exit_4_is_no_crash(self):
        import supervisor
        self.assertEqual(supervisor.EXIT_LEASE, 4)
        with open(os.path.join(HERE, "supervisor.py"), encoding="utf-8") as f:
            src = f.read()
        self.assertIn("if code == EXIT_LEASE:", src)


class Bot(unittest.TestCase):
    def bot(self):
        from bot import main
        b = mock.Mock()
        b.lease, b.held_back, b.letting, b.lease_over = None, [], set(), False
        b.leader_ok = lambda: main.Bot.leader_ok(b)
        b.hold = lambda m: main.Bot.hold(b, m)
        return main, b

    def test_one_arriving_while_let_through_with_ok_true_waits_behind(self):
        """#code-review on 4e035df (manager: in batch b): ok stays true while the old ones go; a new one waits."""
        main, b = self.bot()
        b.lease = mock.Mock(epoch=1)
        b.lease.ok.return_value = False
        got = []

        async def on_message(m):
            if not b.hold(m):
                got.append(m)
                if m == "one":
                    await on_message("new")  # arrives while "one" is handled; ok is true all along
        b.on_message = on_message
        for m in ("one", "two"):
            asyncio.run(on_message(m))
        b.lease.ok.return_value = True
        with mock.patch.object(main, "STATE", tempfile.mkdtemp()):
            asyncio.run(main.Bot.let_through(b))
        self.assertEqual(got, ["one", "two", "new"])
        self.assertEqual(b.held_back, [])

    def test_a_fence_ends_the_bot(self):
        main, b = self.bot()
        b.lease = mock.Mock(epoch=1)
        b.lease.epoch_known.return_value = False
        b.lease.ok.return_value = True
        state = tempfile.mkdtemp()
        with open(os.path.join(state, "fenced"), "w") as f:
            f.write("2")
        with mock.patch.object(main, "STATE", state):
            self.assertTrue(b.leader_ok(), "not before its own epoch is known (#code-review on ccd8baa)")
        os.remove(os.path.join(state, "fenced"))
        b.lease.epoch_known.return_value = True  # (after its first edit, or a restart's first read: lease.epoch_known())
        state = tempfile.mkdtemp()
        b.loop.create_task = lambda coro: coro.close()
        with mock.patch.object(main, "STATE", state):
            self.assertTrue(b.leader_ok())
            with open(os.path.join(state, "fenced"), "w") as f:
                f.write("2")
            self.assertFalse(b.leader_ok())
        self.assertTrue(b.lease_over)

    def test_a_lease_task_that_ends_is_a_crash(self):
        main, b = self.bot()
        b.lease = mock.Mock()
        b.is_closed.return_value = False
        b.loop.create_task = lambda coro: coro.close()
        task = mock.Mock()
        task.cancelled.return_value = False
        main.Bot.lease_ended(b, task)
        self.assertEqual(b.exit_code, 3, "4 is the place given up (#code-review-2 on ccd8baa)")

    def test_the_gate_note(self):
        import time
        main, b = self.bot()
        state = tempfile.mkdtemp()
        for kind, since, said in (("setup", 0, True), ("connection", 60, False), ("connection", 900, True)):
            with open(os.path.join(state, "lease-gate-note.json"), "w") as f:
                json.dump({"kind": kind, "since": time.time() - since, "why": "x"}, f)
            with mock.patch.object(main, "STATE", state):
                self.assertEqual(bool(main.Bot.gate_note(b)), said, (kind, since))

    def test_the_takeover_audit_line(self):
        main, b = self.bot()
        state = tempfile.mkdtemp()
        with open(os.path.join(state, "took-over.json"), "w") as f:
            json.dump({"epoch": 2, "primary": "host-a", "primary_lt": "2026-11-08T00:00:00+00:00", "ssh": "ssh 不通"}, f)
        with mock.patch.object(main, "STATE", state), mock.patch.object(main.remote, "this_machine", lambda: "host-b"):
            line = main.Bot.took_over(b)
        self.assertTrue(line.startswith("接手：host-b（第 2 次換機器），host-a 最後心跳"), line)
        self.assertIn("，ssh 不通，", line)
        self.assertNotIn("ssh ssh", line, "drill 11-09")
        self.assertFalse(os.path.exists(os.path.join(state, "took-over.json")))


if __name__ == "__main__":
    unittest.main()
