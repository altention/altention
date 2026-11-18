"""C-163: what the drill of 11-08 found (part 1), fixed before part 2 (manager): a reply posted twice across a takeover,
the machine that took over not raising its own fencing record, routes an older bot bound outliving the failback,
push_rounds dying when #bot-lease goes."""
import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))))
from altention import testing as _testing  # noqa: E402
_testing.guard_home()  # tests never touch the real home (altention/testing.py; 11-08: one left a real dcremote-owner)
import asyncio
import importlib.machinery
import importlib.util
import json
import os
import shutil
import subprocess
import tempfile
import types
import unittest
from unittest import mock

from bot import main, messages, routecore, tmux_bridge  # (first: it puts the engine on the path)
from altention import config as alt_config  # noqa: E402
from tests import tmuxguard  # noqa: F401  (no test reaches a real tmux server)

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENGINE = os.path.dirname(HERE)


def load_hook():
    loader = importlib.machinery.SourceFileLoader("dchook_fixes", os.path.join(HERE, "tools", "dchook"))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


class Msg(object):
    def __init__(self, author, content):
        self.author, self.content = types.SimpleNamespace(id=author), content


class Seed(unittest.TestCase):
    """Bug A: a turn the bot before this process posted is not posted again (the claim was this process's memory)."""

    def bot(self, history, fail=False):
        b = types.SimpleNamespace(user=types.SimpleNamespace(id=1), SEED_READ=main.Bot.SEED_READ)
        b.claim_turn = types.MethodType(main.Bot.claim_turn, b)
        b.seed_turns = types.MethodType(main.Bot.seed_turns, b)
        reads = []

        async def hist(limit=50):
            reads.append(limit)
            if fail:
                raise RuntimeError("Discord out of reach")
            for m in history:
                yield m
        ch = types.SimpleNamespace(id=11, history=hist)
        return b, ch, reads

    def test_a_turn_posted_before_is_claimed(self):
        history = [Msg(1, "ok\n-# 0 秒 · context 0% · host-a · a004e6f8"), Msg(42, "the owner's words · deadbeef")]
        b, ch, reads = self.bot(history)
        asyncio.run(b.seed_turns(ch))
        self.assertFalse(b.claim_turn("11", "a004e6f8-1111-2222"), "posted by the bot before: not again")
        self.assertTrue(b.claim_turn("11", "deadbeef-0000"), "an id in someone else's message counts for nothing")
        asyncio.run(b.seed_turns(ch))
        self.assertEqual(reads, [main.Bot.SEED_READ], "once per channel")

    def test_a_failed_read_is_tried_again(self):
        b, ch, reads = self.bot([], fail=True)
        with mock.patch("traceback.print_exc"):
            asyncio.run(b.seed_turns(ch))
            asyncio.run(b.seed_turns(ch))
        self.assertEqual(len(reads), 2)

    def test_the_stop_path_seeds_before_it_claims(self):
        import inspect
        src = inspect.getsource(tmux_bridge.TmuxBridge)
        self.assertLess(src.index("await seed(ch)"), src.index("not self.host.claim_turn(str(ch.id), ev[\"prompt_id\"])"))
        self.assertIn('foot = messages.lease("replayed_footer"', src, "a turn of the old bot machine is marked as a replay")
        self.assertIn("換機器時補貼", messages.lease("replayed_footer", tail="host-b · a004e6f8"))


class Owner(unittest.TestCase):
    """Bug B: the machine that becomes the bot raises its own dcremote-owner to the new epoch."""

    def setUp(self):
        self.home = tempfile.mkdtemp()
        p = mock.patch.dict(os.environ, {"HOME": self.home})
        p.start()
        self.addCleanup(p.stop)

    def owner(self):
        with open(alt_config.dcremote_owner_path()) as f:
            return int(f.read())

    def test_raised_never_lowered(self):
        self.assertTrue(alt_config.raise_dcremote_owner(6))
        self.assertEqual(self.owner(), 6)
        self.assertFalse(alt_config.raise_dcremote_owner(5))
        self.assertFalse(alt_config.raise_dcremote_owner(6))
        self.assertEqual(self.owner(), 6)

    def test_the_same_file_dcremote_reads(self):
        loader = importlib.machinery.SourceFileLoader("dcremote_fixes", os.path.join(HERE, "tools", "dcremote"))
        spec = importlib.util.spec_from_loader(loader.name, loader)
        mod = importlib.util.module_from_spec(spec)
        loader.exec_module(mod)
        self.assertEqual(mod.owner_path(), alt_config.dcremote_owner_path())
        alt_config.raise_dcremote_owner(7)
        self.assertTrue(mod.fenced(6), "an older bot's request refused at once")

    def test_telling_this_machine_raises_it_and_drops_older_routes(self):
        tmp = tempfile.mkdtemp()
        with mock.patch.object(routecore, "ROUTES", os.path.join(tmp, "routes.json")):
            routecore.bind_key("/s|%0", "55", "111", epoch=6)
            routecore.bind_key("/s|%1", "56", "112")
            b = types.SimpleNamespace(TELL_SECS=10, channels_state={})
            with mock.patch.object(main.remote, "this_machine", lambda: "host-a"), \
                    mock.patch.object(main.remote, "inventory", lambda: {"machines": {"host-a": {"ssh": ["local"]}}}):
                done = asyncio.run(main.Bot.tell_hosts(b, "host-a", 7, ["host-a"]))
            self.assertEqual(done, {"host-a"})
            self.assertEqual(self.owner(), 7)
            self.assertEqual(sorted(routecore.load()), ["/s|%1"], "the older bot's route gone, this machine's own kept")


class OwnAttached(unittest.TestCase):
    """#code-review-2 on ac4e72e (manager): after a failback the primary's own attached pane, bound meanwhile by the
    standby with its epoch, keeps its route when the primary becomes the bot again (drop_older keeps what a channel
    of this bot names)."""

    def test_failback_keeps_the_primarys_own_attached_pane(self):
        home, tmp = tempfile.mkdtemp(), tempfile.mkdtemp()
        with mock.patch.dict(os.environ, {"HOME": home}), \
                mock.patch.object(routecore, "ROUTES", os.path.join(tmp, "state", "routes.json")):
            routecore.bind_key("/u|%4", "60", "20", epoch=5)   # the owner's pane here, bound by the standby (E+1)
            routecore.bind_key("/b|%0", "61", "21", epoch=5)   # a pane number no channel names now (the drill's)
            b = types.SimpleNamespace(TELL_SECS=10, channels_state={
                "60": {"pane": {"socket": "/u", "id": "%4", "pid": "20"}},     # host gone at the failback
                "62": {"pane": {"host": "host-c", "socket": "/x", "id": "%1"}}})
            with mock.patch.object(main.remote, "this_machine", lambda: "host-a"), \
                    mock.patch.object(main.remote, "inventory", lambda: {"machines": {"host-a": {"ssh": ["local"]}}}):
                asyncio.run(main.Bot.tell_hosts(b, "host-a", 6, ["host-a"]))  # (E+2)
            self.assertEqual(sorted(routecore.load()), ["/u|%4"], "its own attached pane kept, the unnamed one gone")
            hook = load_hook()
            with mock.patch.dict(os.environ, {"TMUX_PANE": "%4", "TMUX": "/u,1,0"}), \
                    mock.patch.object(hook, "ancestors", lambda: [(30, "python3"), (20, "claude")]):
                self.assertEqual(hook.routed(tmp), "60", "its replies still reach the channel")


class TakeoverOwnAttached(unittest.TestCase):
    """#code-review on 0286bb0 (manager): at a takeover tell_hosts(me) may run before localize_own() takes the
    standby's own mark off its attached panes: a pane still marked host=this machine is kept too."""

    def test_a_pane_still_marked_with_this_machine_is_kept(self):
        home, tmp = tempfile.mkdtemp(), tempfile.mkdtemp()
        with mock.patch.dict(os.environ, {"HOME": home}), \
                mock.patch.object(routecore, "ROUTES", os.path.join(tmp, "state", "routes.json")):
            routecore.bind_key("/u|%2", "70", "30", epoch=1)  # bound here by the primary, epoch E
            b = types.SimpleNamespace(TELL_SECS=10, channels_state={
                "70": {"pane": {"host": "host-b", "socket": "/u", "id": "%2", "pid": "30"}}})  # (localize_own not yet)
            with mock.patch.object(main.remote, "this_machine", lambda: "host-b"), \
                    mock.patch.object(main.remote, "inventory", lambda: {"machines": {"host-b": {"ssh": ["local"]}}}):
                asyncio.run(main.Bot.tell_hosts(b, "host-b", 2, ["host-b"]))  # (E+1)
            self.assertEqual(sorted(routecore.load()), ["/u|%2"])


class Routes(unittest.TestCase):
    """The route an older bot bound (with its epoch) is left alone by the hook once a newer bot was seen here."""

    def setUp(self):
        self.home = tempfile.mkdtemp()
        folder = os.path.join(self.home, ".local", "share", "altention")
        os.makedirs(folder)
        with open(os.path.join(folder, "dcremote-owner"), "w") as f:
            f.write("5")

    def test_bind_over_a_real_shell_records_the_epoch(self):
        """manager 11-08: what goes to another machine's shell has a test through a real sh -c. A copy of the engine
        (the route table is beside the tools), a scratch HOME."""
        copy = tempfile.mkdtemp()
        shutil.copytree(os.path.join(ENGINE, "altention"), os.path.join(copy, "altention"),
                        ignore=shutil.ignore_patterns("tests", "__pycache__"))
        shutil.copytree(HERE, os.path.join(copy, "dcbot"), ignore=shutil.ignore_patterns("tests", "state", ".venv", "__pycache__"))
        cmd = "cd %s/dcbot && python3 tools/dcremote --epoch 6 bind '/s|%%0' 55 111" % copy
        r = subprocess.run(["sh", "-c", cmd], env=dict(os.environ, HOME=self.home), capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        with open(os.path.join(copy, "dcbot", "state", "routes.json")) as f:
            table = json.load(f)
        self.assertEqual(table["/s|%0"]["epoch"], 6)
        with mock.patch.dict(os.environ, {"HOME": self.home}):
            hook = load_hook()
            entry = table["/s|%0"]
            self.assertFalse(hook.stale(entry, [(30, "python3")]), "this machine has seen epoch 6 (the request raised it)")
            with open(os.path.join(self.home, ".local", "share", "altention", "dcremote-owner"), "w") as f:
                f.write("7")
            self.assertFalse(hook.stale(entry, [(30, "python3"), (111, "claude")]), "older, still naming its pane: kept")
            self.assertTrue(hook.stale(entry, [(30, "python3"), (222, "claude")]), "older, another pane now: left alone")

    def test_entries_without_an_epoch_stay(self):
        with mock.patch.dict(os.environ, {"HOME": self.home}):
            hook = load_hook()
            chain = [(30, "python3")]
            self.assertFalse(hook.stale({"channel": "55", "pid": "1"}, chain))
            self.assertFalse(hook.stale("56", chain))
            self.assertTrue(hook.stale({"channel": "57", "pid": "2", "epoch": 4}, chain))
            self.assertFalse(hook.stale({"channel": "58", "pid": "", "epoch": 4}, chain), "no process to tell: kept")

    def test_an_older_route_still_naming_its_pane_routes(self):
        """#code-review, #code-review-2 on 7ab6d1a (manager: no gap before the rebind): a machine with an attached
        pane bound at epoch 1 sees a new bot's epoch 3 first: the pane's events still find their channel."""
        tmp = tempfile.mkdtemp()
        os.makedirs(os.path.join(tmp, "state"))
        with open(os.path.join(tmp, "state", "routes.json"), "w") as f:
            json.dump({"/s|%0": {"channel": "55", "pid": "20", "epoch": 1}}, f)
        with open(os.path.join(self.home, ".local", "share", "altention", "dcremote-owner"), "w") as f:
            f.write("3")
        with mock.patch.dict(os.environ, {"HOME": self.home, "TMUX_PANE": "%0", "TMUX": "/s,1,0"}):
            hook = load_hook()
            with mock.patch.object(hook, "ancestors", lambda: [(30, "python3"), (20, "claude")]):
                self.assertEqual(hook.routed(tmp), "55")

    def test_routed_uses_the_live_table(self):
        tmp = tempfile.mkdtemp()
        os.makedirs(os.path.join(tmp, "state"))
        with open(os.path.join(tmp, "state", "routes.json"), "w") as f:
            json.dump({"/s|%0": {"channel": "55", "pid": "999", "epoch": 4}}, f)
        with mock.patch.dict(os.environ, {"HOME": self.home, "TMUX_PANE": "%0", "TMUX": "/s,1,0"}):
            hook = load_hook()
            with mock.patch.object(hook, "ancestors", lambda: [(30, "python3"), (20, "claude")]):
                self.assertIsNone(hook.routed(tmp), "the older bot's binding no longer names a channel")


class PushWithoutLease(unittest.TestCase):
    """#bot-lease gone while push_rounds waits: it ends, push_state sends nothing (drill 11-08: AttributeError)."""

    def test_push_state_with_no_lease(self):
        b = types.SimpleNamespace(lease=None)
        with mock.patch.object(main.remote, "inventory", lambda: {"machines": {"host-a": {"roles": ["dcbot"]},
                                                                               "host-b": {"roles": ["dcbot-standby"]}}}), \
                mock.patch.object(main.remote, "this_machine", lambda: "host-a"):
            self.assertEqual(asyncio.run(main.Bot.push_state(b)), {})

    def test_push_rounds_ends_when_the_lease_goes(self):
        from bot import standby
        b = types.SimpleNamespace(lease=object(), is_closed=lambda: False, channels_state={},
                                  push_state=mock.AsyncMock())

        def gone():
            b.lease = None
            return True
        b.leader_ok = gone
        with mock.patch.object(standby, "CHANGED_SECS", 0):  # (each round at once: the lease goes in the first)
            asyncio.run(asyncio.wait_for(main.Bot.push_rounds(b), 5))  # (ends: no AttributeError, no endless loop)



class PushWhenAQuestionChanges(unittest.TestCase):
    """Drill item 12 (mac, 11-10): a question 7 s after a push, the bot down 26 s later: the standby had no such
    question and its button said "expired". A question opened or closed wakes push_rounds and is pushed at once."""

    def test_opened_and_closed_pushed_at_once_answered_in_part_not(self):
        import tempfile
        from bot import ask, standby
        pushes = []
        b = types.SimpleNamespace(lease=object(), is_closed=lambda: False, leader_ok=lambda: True, channels_state={})

        async def push_state():
            pushes.append(tuple(ask.ids()))
        b.push_state = push_state

        async def go():
            task = asyncio.ensure_future(main.Bot.push_rounds(b))
            await asyncio.sleep(0.05)
            ask.changed()  # (the first round: everything, never pushed before)
            await asyncio.sleep(0.1)
            ask.save("q1", {"channel": 1, "answers": {}})
            await asyncio.sleep(0.1)
            ask.save("q1", {"channel": 1, "answers": {"0": "a"}})  # (answered in part: the same questions)
            await asyncio.sleep(0.1)
            ask.forget("q1")
            await asyncio.sleep(0.1)
            b.lease = None
            ask.changed()
            await asyncio.wait_for(task, 2)
        with mock.patch.object(ask, "ASKS", tempfile.mkdtemp()), mock.patch.object(standby, "CHANGED_SECS", 3600), \
                mock.patch.object(ask.awaiting, "note", lambda: None):
            asyncio.run(go())
        self.assertEqual(pushes, [(), ("q1",), ()], "not waiting CHANGED_SECS (an hour here) nor PUSH_SECS")
        self.assertIsNone(ask.CHANGED, "the bot's hook taken off as push_rounds ends")


if __name__ == "__main__":
    unittest.main()


class HomeGuard(unittest.TestCase):
    """manager 11-08: dcbot's tests run in a throwaway HOME too (altention.testing.guard_home, called at the top of
    every test file), and the guard's before/after check sees ~/.local/share/altention and ~/.claude/settings.json."""

    def test_this_run_is_not_in_the_real_home(self):
        import pwd
        self.assertNotEqual(os.path.realpath(os.path.expanduser("~")), os.path.realpath(pwd.getpwuid(os.getuid()).pw_dir))

    def test_every_test_file_calls_the_guard(self):
        import glob
        missing = [p for p in glob.glob(os.path.join(HERE, "tests", "test_*.py")) if "guard_home()" not in open(p, encoding="utf-8").read()]
        self.assertEqual(missing, [])

    def test_the_check_sees_share_and_settings(self):
        from altention import testing
        home = tempfile.mkdtemp()
        os.makedirs(os.path.join(home, ".local", "share", "altention"))
        os.makedirs(os.path.join(home, ".claude"))
        before = testing._snapshot(home)
        with open(os.path.join(home, ".local", "share", "altention", "dcremote-owner"), "w") as f:
            f.write("4")
        with open(os.path.join(home, ".claude", "settings.json"), "w") as f:
            f.write("{}")
        after = testing._snapshot(home)
        new = set(after) - set(before)
        self.assertIn(os.path.join(home, ".local", "share", "altention", "dcremote-owner"), new)
        self.assertIn(os.path.join(home, ".claude", "settings.json"), new)


class Rebind(unittest.TestCase):
    """#code-review-2 on 7ab6d1a (manager): after a change of machines the routes the bot needs on other machines are
    bound again with its epoch; only the ones no bot names now stay older and are left alone."""

    def test_rebound_routes_live_and_others_left_alone(self):
        home = tempfile.mkdtemp()
        folder = os.path.join(home, ".local", "share", "altention")
        os.makedirs(folder)
        tmp = tempfile.mkdtemp()
        with mock.patch.dict(os.environ, {"HOME": home}), \
                mock.patch.object(routecore, "ROUTES", os.path.join(tmp, "state", "routes.json")):
            routecore.bind_key("/s|%0", "55", "", epoch=6)  # (an attached pane the bot before bound)
            routecore.bind_key("/s|%3", "57", "999", epoch=6)  # (one no bot names now: another pane by now)
            with open(os.path.join(folder, "dcremote-owner"), "w") as f:
                f.write("7")  # (the new bot's first request came by)
            b = types.SimpleNamespace(channels_state={
                "55": {"pane": {"host": "host-c", "socket": "/s", "id": "%0", "pid": ""}},
                "56": {"pane": {"socket": "/s", "id": "%1"}},  # (this machine's own: no route by ssh)
                "57x": "not a channel"})
            asked = []

            async def dcremote(host, *args, **kw):  # the remote half, as that machine runs it, with epoch 7
                asked.append((host,) + args)
                routecore.bind_key(args[1], args[2], args[3], epoch=7)
                return 0, ""
            with mock.patch.object(main.remote, "dcremote", dcremote):
                asyncio.run(main.Bot.rebind_far(b, 7))
                asyncio.run(main.Bot.rebind_far(b, 7))
            self.assertEqual(asked, [("host-c", "bind", "/s|%0", "55", "")], "once per epoch, only other machines' panes")
            os.makedirs(os.path.join(tmp, "state"), exist_ok=True)
            hook = load_hook()
            with mock.patch.dict(os.environ, {"TMUX_PANE": "%0", "TMUX": "/s,1,0"}), \
                    mock.patch.object(hook, "ancestors", lambda: [(30, "python3"), (20, "claude")]):
                self.assertEqual(hook.routed(tmp), "55", "the route the bot needs is live again")
            with mock.patch.dict(os.environ, {"TMUX_PANE": "%3", "TMUX": "/s,1,0"}), \
                    mock.patch.object(hook, "ancestors", lambda: [(30, "python3"), (20, "claude")]):
                self.assertIsNone(hook.routed(tmp), "the one no bot names now: left alone")

    def test_a_machine_not_reached_is_tried_again(self):
        b = types.SimpleNamespace(channels_state={"55": {"pane": {"host": "host-c", "socket": "/s", "id": "%0"}}})
        codes = [255, 0]

        async def dcremote(host, *args, **kw):
            return codes.pop(0), ""
        with mock.patch.object(main.remote, "dcremote", dcremote):
            asyncio.run(main.Bot.rebind_far(b, 7))
            asyncio.run(main.Bot.rebind_far(b, 7))
        self.assertEqual(codes, [], "asked again after the first failed")

    def test_announce_rebinds(self):
        import inspect
        self.assertIn("await self.rebind_far(epoch)", inspect.getsource(main.Bot.announce_host))


class LeaseReadFail(unittest.TestCase):
    """The drill's item 15 switch (manager 11-08): reads of #bot-lease fail while the named file is there, in the rig
    alone."""

    def setUp(self):
        import pwd
        self.real = pwd.getpwuid(os.getuid()).pw_dir
        self.scratch = tempfile.mkdtemp()
        self.flag = os.path.join(self.scratch, "read-fail")

    def fails(self, home, test, value=None):
        from bot import lease
        with mock.patch.dict(os.environ, {"HOME": home, "ALTENTION_STANDBY_TEST": test,
                                          "DCBOT_DRILL_LEASE_READ_FAIL": self.flag if value is None else value}):
            return lease.drill_read_fails()

    def test_only_in_the_rig_and_while_the_file_is_there(self):
        self.assertFalse(self.fails(self.scratch, "1"), "no file: reads as ever")
        open(self.flag, "w").close()
        self.assertTrue(self.fails(self.scratch, "1"))
        self.assertFalse(self.fails(self.real, "1"), "the real HOME: never")
        self.assertFalse(self.fails(self.scratch, ""), "no test switch: never")
        self.assertFalse(self.fails(self.scratch, "1", value=""))
        from altention import standby as alt
        with mock.patch.object(alt, "READY", True):
            self.assertFalse(self.fails(self.real, ""), "READY never opens it")

    def test_a_read_and_a_write_fail(self):
        from bot import lease
        open(self.flag, "w").close()
        lz = lease.Lease("host-a", ["host-a", "host-b"], lambda: None, 1)
        with mock.patch.dict(os.environ, {"HOME": self.scratch, "ALTENTION_STANDBY_TEST": "1", "DCBOT_DRILL_LEASE_READ_FAIL": self.flag}):
            self.assertIsNone(asyncio.run(lz.round()), "the round's read fails")
            self.assertIsNone(lz.write_ok, "its write reads its message first: failed too")

    def test_said_in_audit(self):
        import inspect
        self.assertIn('"drill_read_fail_on" if in_drill_rig() else "drill_read_fail_off"', inspect.getsource(main.Bot.on_ready))
        self.assertIn("請把它拿掉", messages.lease("drill_read_fail_off"))


class ClaudeThere(unittest.TestCase):
    """The low the manager ruled (drill 11-08): a window on another machine runs that machine's claude by its full
    path, not by a PATH a non-interactive ssh may lack; a window that does not come up says why."""

    def test_dcremote_info_over_a_real_shell_names_it(self):
        home = tempfile.mkdtemp()
        bin_dir = os.path.join(home, ".local", "bin")
        os.makedirs(bin_dir)
        with open(os.path.join(bin_dir, "claude"), "w") as f:
            f.write("#!/bin/sh\n")
        os.chmod(os.path.join(bin_dir, "claude"), 0o755)
        cmd = "PATH=/usr/bin:/bin python3 %s info" % os.path.join(HERE, "tools", "dcremote")
        r = subprocess.run(["sh", "-c", cmd], env=dict(os.environ, HOME=home), capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout)["claude"], os.path.join(bin_dir, "claude"), "found off the PATH too")

    def test_claude_on_kept_or_the_bare_name(self):
        from bot import remote
        answers = [(0, json.dumps({"claude": "/h/.local/bin/claude"})), (255, "")]
        asked = []

        async def dcremote(host, *args, **kw):
            asked.append(host)
            return answers.pop(0) if answers else (0, "{}")
        with mock.patch.object(remote, "dcremote", dcremote), mock.patch.dict(remote.CLAUDE_AT, {}, clear=True):
            self.assertEqual(asyncio.run(remote.claude_on("host-c")), "/h/.local/bin/claude")
            self.assertEqual(asyncio.run(remote.claude_on("host-c")), "/h/.local/bin/claude")
            self.assertEqual(asyncio.run(remote.claude_on("host-d")), "claude", "not reached: the bare name")
            self.assertEqual(asked, ["host-c", "host-d"], "kept once known")

    def test_the_window_opened_with_it(self):
        import inspect
        src = inspect.getsource(tmux_bridge.TmuxBridge.ensure_home)
        self.assertIn("await remote.claude_on(remote_host)", src)
        self.assertIn("最後的畫面：", src, "a window that did not come up: its last lines in the message")


class NextBatch(unittest.TestCase):
    """The manager's next standard batch (11-08)."""

    def test_the_screen_lines_are_masked(self):
        import inspect
        src = inspect.getsource(tmux_bridge.TmuxBridge.ensure_home)
        self.assertLess(src.index("masking.masked(await pane.screen()"), src.index('why.append("最後的畫面："'),
                        "masked before it is cut (#code-review-2 on 60a8796)")
        from bot import masking
        secret = "sk-ant-api03-" + "x" * 40
        self.assertNotIn(secret, masking.masked("export ANTHROPIC_API_KEY=" + secret), "masked() hides such a line")

    def test_a_long_token_across_the_cut_is_masked(self):
        """The screen masked whole, then cut at 120 per line: a token running past the cut leaves no half of it."""
        from bot import masking
        token = "sk-ant-api03-" + "A1b2C3d4" * 20  # (longer than the 120 kept)
        screen = "error\n" + "  ANTHROPIC_API_KEY=" + token + "\n❯"
        shown = masking.masked(screen)
        tail = [l for l in shown.splitlines() if l.strip()][-3:]
        line = " / ".join(t.strip()[:120] for t in tail)
        self.assertNotIn("A1b2C3d4A1b2", line)
        self.assertNotIn(token[:40], line)

    def test_a_failed_open_asks_that_machines_claude_again(self):
        import inspect
        src = inspect.getsource(tmux_bridge.TmuxBridge.ensure_home)
        self.assertLess(src.index("remote.CLAUDE_AT.pop(remote_host, None)"), src.index("在 tmux `%s` 裡開不起 Claude"))

    def test_the_step_down_note_once_soon_after_coming_up(self):
        import time as _time
        b = types.SimpleNamespace(online_at=_time.time())
        self.assertIn("它沒有接手", main.stepped_down_note(b))
        self.assertEqual(main.stepped_down_note(b), "", "once")
        b.online_at = _time.time() - 3600
        self.assertEqual(main.stepped_down_note(b), "", "long after coming up: nothing")


class Discussed(unittest.TestCase):
    """manager 11-08 (a): the 💬 marker in the first line, or in the first line of the last paragraph; not quoted."""

    def test_first_line(self):
        from bot import calls
        self.assertEqual(calls.discussed("💬 要跟你討論：裝 Node 嗎？\n\n內文"), "裝 Node 嗎？")

    def test_last_paragraph(self):
        from bot import calls
        text = "**差別**：舊規則…\n\n一、攻擊\n- 一般消行\n\n💬 要跟你討論：要不要讓我在 host-b 裝 Node？\n比對攻擊數。"
        self.assertEqual(calls.discussed(text), "要不要讓我在 host-b 裝 Node？", "#rulesets 11-08")

    def test_quoted_in_the_text_says_nothing(self):
        from bot import calls
        text = "說明：回覆的第一行用「💬 要跟你討論：」開頭才會 @。\n💬 要跟你討論：這行在中間\n\n最後一段沒有記號。"
        self.assertIsNone(calls.discussed(text))


class ForeignWindow(unittest.TestCase):
    """manager 11-08 (1): after a failback the primary finds, under its own session name, a window the standby
    opened there (no --settings: its hooks went by a route that is gone): closed and opened again with --resume."""

    def test_session_command_reads_the_panes_process(self):
        from bot import tmuxio
        child = subprocess.Popen([_sys.executable, "-c", "import time; time.sleep(30)", "--settings", "/x/claude-settings.json"])
        self.addCleanup(lambda: (child.kill(), child.wait()))
        import time as _t
        for _ in range(100):  # (until the child has exec'd: its command line is the new one)
            try:
                if b"--settings" in open("/proc/%d/cmdline" % child.pid, "rb").read():
                    break
            except OSError:
                break  # (no /proc: macOS, ps is read instead)
            _t.sleep(0.05)

        async def tmux(*args, **kw):
            return 0, "%d\n" % child.pid
        with mock.patch.object(tmuxio, "tmux", tmux):
            got = asyncio.run(tmuxio.session_command("dc-x-1"))
        self.assertIn("--settings /x/claude-settings.json", got)

        async def none(*args, **kw):
            return 1, ""
        with mock.patch.object(tmuxio, "tmux", none):
            self.assertIsNone(asyncio.run(tmuxio.session_command("dc-x-1")))

    def foreign(self, cmd, standby_on):
        b = types.SimpleNamespace(host=types.SimpleNamespace(standby_on=standby_on))

        async def sc(name):
            return cmd
        with mock.patch.object(tmux_bridge.tmuxio, "session_command", sc):
            return asyncio.run(tmux_bridge.TmuxBridge.foreign_window(b, "dc-x-1"))

    def test_foreign_by_the_settings_flag_with_a_standby(self):
        self.assertFalse(self.foreign("claude --settings /any/path/claude-settings.json --resume s", True),
                         "--settings in any spelling of the path: this bot's")
        self.assertTrue(self.foreign("claude --model haiku --resume s", True), "opened by the other bot: no --settings")
        self.assertFalse(self.foreign(None, True), "cannot tell: nothing closed on a guess")
        self.assertFalse(self.foreign("claude --settings=/a/claude-settings.json --resume s", True), "the = spelling too")
        self.assertFalse(self.foreign("claude --settings /s/claude-settings.0123456789ab.json --resume s", True),
                         "the copy with its attachments folder: a file, this bot's")
        self.assertTrue(self.foreign('claude --model haiku --settings {"permissions":{"additionalDirectories":["/a/5"]}} '
                                     "--resume s", True), "opened by the other bot: --settings with its folder alone, as JSON")
        self.assertTrue(self.foreign('claude --settings={"permissions":{"additionalDirectories":["/a b/5"]}} --resume s', True),
                        "JSON in the = spelling, a folder with a space")
        self.assertTrue(self.foreign("claude --settings", True), "a --settings with nothing after it names no file")

    def test_never_without_a_standby(self):
        """#code-review on 3223faf (manager): without a standby nothing changes, even a window with no --settings."""
        self.assertFalse(self.foreign("claude --model haiku --resume s", False))
        self.assertFalse(self.foreign("claude --model haiku --resume s", None))

    def test_ensure_takes_such_a_window_for_a_stale_one(self):
        import inspect
        src = inspect.getsource(tmux_bridge.TmuxBridge.ensure)
        i, j = src.index("await self.foreign_window(name)"), src.index('st["cc_stale"] = True')
        self.assertLess(i, j)
        self.assertLess(j, src.index("await self.close(ch)"), "then closed and opened again (--resume), as C-162's")
