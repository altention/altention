"""C-79: Claude Code started again in an attached pane (respawn-pane, or a pane that is Claude itself, with no shell
around it): the pane's process is not the one the route has. Until now the hook dropped every event of it and the
bot would not type into it: the owner had to !detach and !attach. Now the hook passes the event on with the pane's
process ("dc_respawned"), and the bot takes it, and moves the route to that process, when it goes on with the
channel's conversation (`--resume <id>` and `-c` keep the id: measured, 2.1.306). Any other conversation is left
out, as before."""
import asyncio
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from bot import ask, config, events, routecore, routes, tmux_bridge, tmuxio
from tests import fakepane
from tests.test_turn_end import Ch, Host

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PANE_CODE = ("import os, subprocess, sys; open(os.environ['PANE_PID_FILE'], 'w').write(str(os.getpid())); "
             "a = sys.argv[1:]; a = a[2:] if a[:1] == ['--settings'] else a; "  # (stands for a claude --settings X)
             "sys.exit(subprocess.run(a, input=sys.stdin.buffer.read()).returncode)")


class Hook(unittest.TestCase):
    """tools/dchook --global, run as the child of a pane's process whose parent is named tmux (as a tmux server is):
    the hook's own ancestors are what it reads, as in a real pane."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="respawned ")
        self.addCleanup(self.tmp.cleanup)
        for d in ("tools", "state"):
            os.makedirs(os.path.join(self.tmp.name, d))
        self.hook = os.path.join(self.tmp.name, "tools", "dchook")
        shutil.copy(os.path.join(ROOT, "tools", "dchook"), self.hook)
        self.server = os.path.join(self.tmp.name, "tmux")  # a process of this name stands for the tmux server
        os.symlink(sys.executable, self.server)
        self.pid_file = os.path.join(self.tmp.name, "pane.pid")

    def route(self, pid):
        with open(os.path.join(self.tmp.name, "state", "routes.json"), "w") as f:
            json.dump({"/tmp/sock|%7": {"channel": "55", "pid": pid}}, f)

    def fire(self, *flags, ev=None, server=True, local=False, settings=None, **more):
        env = dict((k, v) for k, v in os.environ.items() if not k.startswith(("TMUX", "DCBOT_")))
        env.update(TMUX="/tmp/sock,123,0", TMUX_PANE="%7", PANE_PID_FILE=self.pid_file, **more)
        pane = [sys.executable, "-c", PANE_CODE] + (["--settings", settings] if settings else []) + \
            [sys.executable, self.hook] + ([] if local else ["--global"]) + list(flags)
        argv = ([self.server, "-c", "import subprocess, sys; sys.exit(subprocess.run(sys.argv[1:], input=sys.stdin.buffer.read()).returncode)"]
                if server else []) + pane
        r = subprocess.run(argv, env=env, input=json.dumps(ev or {"hook_event_name": "Stop", "session_id": "S"}).encode(),
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
        self.assertEqual((r.returncode, r.stderr), (0, b""))
        return r.stdout, events.drain(os.path.join(self.tmp.name, "state", "events"))

    def pane_pid(self):
        with open(self.pid_file) as f:
            return f.read()

    def test_a_pane_whose_process_is_another_is_passed_on_with_it(self):
        self.route("999999999")
        _, got = self.fire()
        self.assertEqual([(e["channel"], e.get("dc_routed"), e.get("dc_respawned")) for e in got], [("55", True, self.pane_pid())])
        self.assertEqual(got[0]["dc_pane"], {"socket": "/tmp/sock", "pane": "%7"}, "the pane as before: dc_pane keeps its shape")

    def test_the_pane_the_route_has_goes_as_before(self):
        self.route(str(os.getpid()))  # this test is among the hook's ancestors: the route's process is there
        _, got = self.fire()
        self.assertEqual([(e["channel"], "dc_respawned" in e) for e in got], [("55", False)])
        self.route("")
        _, got = self.fire()
        self.assertEqual([(e["channel"], "dc_respawned" in e) for e in got], [("55", False)], "a route with no pid: as before")

    def test_a_question_or_a_plan_of_a_process_the_route_does_not_have_is_left_to_the_terminal(self):
        """--ask waits for its answer: such a process gets the terminal's own menu, as before (its SessionStart
        came first and moved the route if it is the channel's conversation)."""
        self.route("999999999")
        question = {"hook_event_name": "PreToolUse", "tool_name": "AskUserQuestion", "session_id": "S",
                    "tool_input": {"questions": [{"question": "Which?"}]}}
        self.assertEqual(self.fire("--ask", ev=question), (b"", []))
        plan = {"hook_event_name": "PermissionRequest", "tool_name": "ExitPlanMode", "session_id": "S", "tool_input": {"plan": "x"}}
        self.assertEqual(self.fire("--plan", ev=plan), (b"", []))

    def test_a_window_the_bot_opened_for_another_channel_that_got_the_number_reports_by_its_own_hook(self):
        """#code-review on 4c0a7e2: an old route %7 -> 55 whose pid is gone, and %7 now a window the bot opened for
        channel 77 (DCBOT_CHANNEL, its own --settings hook): its events go to 77 by its own hook, the global hook
        stays silent, as before C-79. Before this fix both stayed silent (the global one sent them to 55, marked,
        and the bot dropped them)."""
        self.route("999999999")
        mine = os.path.join(self.tmp.name, "state", "claude-settings.json")  # the bot's --settings (this hook's state)
        bot_window = {"DCBOT_CHANNEL": "77", "DCBOT_ROOT": self.tmp.name, "settings": mine}
        _, got = self.fire(local=True, **bot_window)
        self.assertEqual([(e["channel"], "dc_respawned" in e) for e in got], [("77", False)])
        self.assertEqual(self.fire(**bot_window), (b"", []), "the global hook of that process: nothing")
        same = {"DCBOT_CHANNEL": "55", "DCBOT_ROOT": self.tmp.name, "settings": mine}  # the channel's own window, attached since
        self.assertEqual(self.fire(local=True, **same), (b"", []), "its own hook: the table's one reports it")
        _, got = self.fire(**same)
        self.assertEqual([(e["channel"], e.get("dc_respawned")) for e in got], [("55", self.pane_pid())])

    def test_an_owners_pane_that_inherited_another_channels_variable_is_judged_by_the_bot(self):
        """DCBOT_CHANNEL alone does not make a window the bot's (a tmux server started from a bot window passes it on
        to the owner's panes): with no bot --settings on the command line the global hook passes it on marked, and
        the bot goes by the session; another --settings file is not the bot's either."""
        self.route("999999999")
        for settings in (None, os.path.join(self.tmp.name, "elsewhere", "claude-settings.json")):
            _, got = self.fire(DCBOT_CHANNEL="77", settings=settings)
            self.assertEqual([(e["channel"], e.get("dc_respawned")) for e in got], [("55", self.pane_pid())], settings)

    def test_pane_process_is_the_one_under_the_server(self):
        """Read from the hook itself, not from a real tmux (a test may run inside one, or not)."""
        import importlib.machinery
        import importlib.util
        loader = importlib.machinery.SourceFileLoader("dchook_respawned", self.hook)
        mod = importlib.util.module_from_spec(importlib.util.spec_from_loader("dchook_respawned", loader))
        loader.exec_module(mod)
        self.assertEqual(mod.pane_process([(30, "python3"), (20, "zsh"), (10, "tmux: server"), (1, "systemd")]), 20)
        self.assertEqual(mod.pane_process([(30, "python3"), (20, "claude"), (10, "tmux")]), 20, "macOS: ps names it tmux")
        self.assertIsNone(mod.pane_process([(30, "python3"), (20, "sshd"), (1, "init")]), "no tmux above: no pane")
        self.assertIsNone(mod.pane_process([]))
        table = os.path.join(self.tmp.name, "state", "routes.json")
        self.route("999999999")
        with mock.patch.dict(os.environ, {"TMUX": "/tmp/sock,1,0", "TMUX_PANE": "%7"}):
            for name in ("DCBOT_CHANNEL", "DCBOT_ROOT"):  # (a test run from a channel's own Claude has them; restored after)
                os.environ.pop(name, None)
            with mock.patch.object(mod, "ancestors", lambda: [(30, "python3"), (20, "sshd"), (1, "init")]):
                where = {}
                self.assertIsNone(mod.routed(self.tmp.name, where), "no pane process found: no channel, as before")
            with mock.patch.object(mod, "ancestors", lambda: [(30, "python3"), (20, "claude"), (10, "tmux: server")]):
                where = {}
                self.assertEqual((mod.routed(self.tmp.name, where), where.get("respawned")), ("55", "20"))
                self.assertIsNone(mod.routed(self.tmp.name), "asked with no `where`: no channel, as before")
        self.assertTrue(os.path.exists(table))


class Bot(unittest.TestCase):
    """The bot's side: stranger() and repin(), in handle() and waits()."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="respawned bot ")
        self.addCleanup(self.tmp.cleanup)
        saved = (ask.ASKS, ask.ANSWERS, config.OWNER_ID, config.INTERIM, config.STATE)
        ask.ASKS, ask.ANSWERS = os.path.join(self.tmp.name, "asks"), os.path.join(self.tmp.name, "answers")
        config.OWNER_ID, config.INTERIM, config.STATE = 42, 0, self.tmp.name
        self.addCleanup(lambda: (setattr(ask, "ASKS", saved[0]), setattr(ask, "ANSWERS", saved[1]), setattr(config, "OWNER_ID", saved[2]),
                                 setattr(config, "INTERIM", saved[3]), setattr(config, "STATE", saved[4])))
        p = mock.patch.object(routecore, "ROUTES", os.path.join(self.tmp.name, "routes.json"))
        p.start()
        self.addCleanup(p.stop)
        self.ch = Ch()
        self.host = Host(self.ch)
        self.pane = {"socket": "/tmp/sock", "id": "%7", "pid": "111", "session": "work", "window": "1"}
        self.host.channels_state["5"] = {"pane": self.pane, "cwd": "/w", "session_id": "S", "mode": "tmux"}
        routes.bind(self.pane, 5)
        self.bridge = tmux_bridge.TmuxBridge(self.host)

    def ev(self, kind, sid="S", **kw):
        return dict({"channel": "5", "hook_event_name": kind, "session_id": sid, "dc_routed": True,
                     "dc_pane": {"socket": "/tmp/sock", "pane": "%7"}, "dc_respawned": "222"}, **kw)

    def table(self):
        return routecore.load()

    def turn(self, sid="S", **kw):
        async def go():
            await self.bridge.handle(self.ev("SessionStart", sid, source="resume", **kw))
            await self.bridge.handle(self.ev("UserPromptSubmit", sid, prompt="go", **kw))
            await self.bridge.handle(self.ev("Stop", sid, last_assistant_message="REPLY", **kw))
            await asyncio.sleep(0.05)
        asyncio.run(go())

    def test_the_channels_conversation_in_a_new_process_is_taken_and_the_route_follows_it(self):
        self.turn()
        self.assertTrue([m for m in self.ch.sent if (m.content or "").startswith("REPLY")], [m.content for m in self.ch.sent])
        self.assertEqual(self.pane["pid"], "222")
        self.assertEqual(self.table(), {"/tmp/sock|%7": {"channel": "5", "pid": "222"}})

    def test_another_conversation_is_left_out_and_nothing_moves(self):
        self.turn("NEW")
        self.assertEqual([m.content for m in self.ch.sent], [])
        self.assertEqual((self.pane["pid"], self.host.channels_state["5"]["session_id"]), ("111", "S"))
        self.assertEqual(self.table(), {"/tmp/sock|%7": {"channel": "5", "pid": "111"}})
        self.assertFalse(self.bridge.waits(tmux_bridge.cc_hooks.normalize(self.ev("Stop", "NEW"))), "not held in the spool either")
        with open(os.path.join(self.tmp.name, tmux_bridge.UNPLACED_LOG)) as f:
            self.assertIn("another process in the attached pane", f.read())

    def test_a_channel_that_knows_no_conversation_takes_none(self):
        del self.host.channels_state["5"]["session_id"]
        self.turn()
        self.assertEqual((self.pane["pid"], [m.content for m in self.ch.sent]), ("111", []))

    def test_a_question_of_another_conversation_is_handed_back(self):
        handed = []

        async def deliver(host, ask_id, result):
            handed.append((ask_id, result))
        with mock.patch.object(ask, "deliver_to", deliver):
            asyncio.run(self.bridge.handle(self.ev("Ask", "NEW", ask_id="q1", questions=[{"question": "Which?"}])))  # (an old hook's: today's leaves it to the terminal)
        self.assertEqual(handed, [("q1", {"pass": True})])

    def test_the_end_of_a_session_does_not_move_the_route(self):
        """The old process's end may come after the new one's start: it must not take the route back."""
        asyncio.run(self.bridge.handle(self.ev("SessionEnd", reason="prompt_input_exit")))
        self.assertEqual(self.pane["pid"], "111")
        self.assertEqual(self.table()["/tmp/sock|%7"]["pid"], "111")

    def test_another_machines_route_is_written_there_in_a_task(self):
        self.pane["host"] = "lap"
        bound = []

        async def bind_any(pane, cid):
            bound.append((dict(pane), cid))
        with mock.patch.object(routes, "bind_any", bind_any):
            self.turn(host="lap")
        self.assertEqual([(p["pid"], p["host"], c) for p, c in bound], [("222", "lap", 5)], "once, not for every event")
        self.assertEqual(self.table()["/tmp/sock|%7"]["pid"], "111", "this machine's table is not that pane's")

    def test_after_a_restart_the_pane_is_typed_into_and_its_events_come_unmarked(self):
        """The bot's state and the route table keep the new process: a bot started again types into the pane (alive()
        checks the pane's process) and the hook finds the route's process among its ancestors."""
        self.turn()
        again = tmux_bridge.TmuxBridge(self.host)
        shown = {"pid": "222"}

        async def tmux(*args, **kw):
            return 0, "2.1.306\t%s\n" % shown["pid"]
        with fakepane.use(tmux=tmux):
            self.assertTrue(asyncio.run(tmuxio.alive(self.host.channels_state["5"]["pane"])))
            shown["pid"] = "111"
            self.assertFalse(asyncio.run(tmuxio.alive(self.host.channels_state["5"]["pane"])), "the old process: as before")
        plain = {k: v for k, v in self.ev("Stop", last_assistant_message="AFTER").items() if k != "dc_respawned"}
        asyncio.run(again.handle(plain))
        self.assertTrue([m for m in self.ch.sent if (m.content or "").startswith("AFTER")])


if __name__ == "__main__":
    unittest.main()
