"""Routing between channels and the owner's own tmux panes: the table, the hook's lookup, discovery."""
import asyncio
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from bot import config, events, routecore, routes, tmuxio
from bot.tmux_bridge import HOOK_EVENTS, target
from tests import tmuxguard  # noqa: F401,E402  (no test reaches a real tmux server)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PANE = {"socket": "/tmp/tmux-1/default", "id": "%7", "session": "work", "window": "2", "window_name": "",
        "cwd": "/somewhere/proj"}


class Table(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old, routecore.ROUTES = routecore.ROUTES, os.path.join(self.tmp.name, "state", "routes.json")

    def tearDown(self):
        routecore.ROUTES = self.old
        self.tmp.cleanup()

    def test_bind_and_unbind(self):
        self.assertEqual(routes.load(), {})
        routes.bind(dict(PANE, pid="111"), 55)
        other = dict(PANE, id="%8")
        routes.bind(other, 66)
        self.assertEqual(routes.load(), {"/tmp/tmux-1/default|%7": {"channel": "55", "pid": "111"},
                                         "/tmp/tmux-1/default|%8": {"channel": "66", "pid": ""}})
        routes.bind(other, 55)  # a channel follows one pane only
        self.assertEqual(routes.load(), {"/tmp/tmux-1/default|%8": {"channel": "55", "pid": ""}})
        self.assertEqual(routes.channel_of("55"), "55", "tables written before pids were kept still work")
        routes.unbind(55)
        self.assertEqual(routes.load(), {})

    def test_the_table_itself_is_one_module_for_the_bot_and_the_remote_tool(self):
        """routes.py hands on routecore's functions; tools/dcremote, which carried a copy of them until stage 3b
        step 1.9, binds and unbinds with the same ones, and what it writes is what it wrote."""
        for name in ("load", "save", "channel_of", "entry", "unbind", "project_name", "bind_key"):
            self.assertIs(getattr(routes, name), getattr(routecore, name), name)
        import importlib.machinery
        import importlib.util
        loader = importlib.machinery.SourceFileLoader("dcremote_routes", os.path.join(ROOT, "tools", "dcremote"))
        tool = importlib.util.module_from_spec(importlib.util.spec_from_loader("dcremote_routes", loader))
        loader.exec_module(tool)
        was = dict(tmuxio._set)
        self.addCleanup(lambda: (tmuxio._set.clear(), tmuxio._set.update(was)))
        with mock.patch.dict(os.environ, {"DCBOT_ENV": os.path.join(self.tmp.name, "none")}):
            tool.main(["bind", "/s|%1", "55", "123"])
            tool.main(["bind", "/s|%2", "66"])
            tool.main(["bind", "/s|%3", "55", "9"])
            self.assertEqual(routecore.load(), {"/s|%2": {"channel": "66", "pid": ""}, "/s|%3": {"channel": "55", "pid": "9"}},
                             "one pane for a channel; no process given: an empty one, as before")
            tool.main(["unbind", "66"])
        self.assertEqual(routecore.load(), {"/s|%3": {"channel": "55", "pid": "9"}})
        self.assertEqual(routecore.load(), routes.load())
        for name in ("load_routes", "save_routes", "channel_of", "project_name", "ROUTES"):
            self.assertFalse(hasattr(tool, name), "the tool's own copy: " + name)

    def test_routes_written_before_a_route_carried_its_panes_process(self):
        table = {"/s|%1": "55", "/s|%2": {"channel": "66"}, "/s|%3": {"channel": "77", "pid": ""}, "/s|%4": {"channel": "88", "pid": "12"},
                 "/s|%5": None}
        self.assertEqual(routecore.without_pid(table), ["/s|%1", "/s|%2", "/s|%3", "/s|%5"])
        self.assertEqual(routecore.without_pid({}), [])
        self.assertEqual([routecore.channel_of(table[k]) for k in sorted(table)], ["55", "66", "77", "88", None], "still read, each of them")

    def test_names_and_listing(self):
        self.assertEqual(routes.channel_name(PANE, "proj"), "work-2")
        self.assertEqual(routes.channel_name(dict(PANE, session="0", window="1"), "My Proj"), "my-proj-0-1")
        self.assertEqual(routes.channel_name(dict(PANE, window_name="Back Test"), "proj"), "work-back-test")
        self.assertEqual(routes.label(dict(PANE, window_name="bt")), "work:bt · /somewhere/proj")
        other = dict(PANE, id="%8", cwd="/else/where")
        self.assertEqual(routes.by_project([PANE, other, dict(PANE, id="%9")], {routes.key(other): "1"}),
                         {"proj": [PANE, dict(PANE, id="%9")]})
        self.assertEqual(routes.project_name("/nowhere/at/all/thing/"), "thing")
        with tempfile.TemporaryDirectory() as tmp:  # the repo's name, also from a worktree (its own repo: the tests
            repo, wt = os.path.join(tmp, "myrepo"), os.path.join(tmp, "wt-branch")  # may run outside any checkout)
            for args in (["init", "-q", repo], ["-C", repo, "commit", "-q", "--allow-empty", "-m", "x"],
                         ["-C", repo, "worktree", "add", "-q", wt]):
                subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com"] + args, check=True,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self.assertEqual(routes.project_name(repo), "myrepo")
            self.assertEqual(routes.project_name(os.path.join(wt, "")), "myrepo")
        other = dict(PANE, id="%8")
        view = routes.buttons([PANE, other, dict(PANE, id="%9", cwd="/x/solo")], {}, now=1000)
        self.assertEqual([(b.label, b.custom_id) for b in view.children],
                         [("📁 整個專案 proj（2 個）", "att:1000:project:proj"),
                          ("work:2（proj）", "att:1000:/tmp/tmux-1/default|%7"),
                          ("work:2（proj）", "att:1000:/tmp/tmux-1/default|%8"),
                          ("work:2（solo）", "att:1000:/tmp/tmux-1/default|%9")])
        self.assertEqual(routes.button_ref("att:1000:project:proj", now=1100), "project:proj")
        self.assertEqual(routes.button_ref("att:1000:/s|%7", now=1000 + routes.BUTTON_SECS + 1), "", "an old list is dead")
        self.assertIsNone(routes.button_ref("ask:1:0:1"))
        self.assertIsNone(routes.button_ref(None))
        self.assertIsNone(routes.buttons([PANE], {routes.key(PANE): "5"}))
        self.assertEqual(routes.pick([PANE], "1"), PANE)
        self.assertEqual(routes.pick([PANE], routes.key(PANE)), PANE)
        self.assertIsNone(routes.pick([PANE], "2"))
        text = routes.listing([PANE], {routes.key(PANE): "55"})
        self.assertIn("`work:2`", text)
        self.assertIn("已接到 <#55>", text)
        self.assertIn("沒有正在跑", routes.listing([], {}))
        self.assertEqual(routes.label(PANE), "work:2 · /somewhere/proj")

    def test_an_archived_channel_is_recognised_for_the_same_pane_only(self):
        C = lambda name, topic: type("C", (), {"name": name, "topic": topic})()  # noqa: E731
        mine = C("work-2", routes.topic("H", PANE))
        chans = [C("work-2", routes.topic("H", dict(PANE, cwd="/other/place"))), mine, C("other", None)]
        self.assertIs(routes.archived_channel(chans, "H", PANE, "proj"), mine)
        self.assertIsNone(routes.archived_channel(chans, "OtherHost", PANE, "proj"))
        self.assertIsNone(routes.archived_channel([], "H", PANE, "proj"))

    def test_a_new_channel_takes_its_category_folder(self):
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            state = {"1": {"cwd": a}, "2": {"cwd": a}, "3": {"cwd": b}, "4": {"cwd": "/no/such/dir"}, "9": {"cwd": b}}
            self.assertEqual(routes.sibling_cwd([1, 2, 3, 4], state), a, "the most common one")
            self.assertEqual(routes.sibling_cwd([4], state), None, "a folder that is gone does not count")
            self.assertIsNone(routes.sibling_cwd([], state))

    def test_target_and_tmux_addressing(self):
        self.assertEqual(target({"tmux": "dc-x"}), "dc-x")
        self.assertEqual(target({"tmux": "dc-x", "pane": {"socket": "/s", "id": "%1"}}), {"socket": "/s", "id": "%1"})
        self.assertEqual(tmuxio.at("dc-x"), (None, "=dc-x:"))
        self.assertEqual(tmuxio.at({"socket": "/s", "id": "%1"}), ("/s", "%1"))

    def test_parse_panes_keeps_only_claude(self):
        out = ("%1\tmain\t0\tzsh\tzsh\t/home/u\t1\t10\n%2\tmain\t1\tclaude\tclaude\t/home/u/p\t1\t20\n"
               "%3\tmain\t2\tbacktest\tclaude\t/home/u/p\t0\t30\n\nbroken line\n")
        self.assertEqual(tmuxio.parse_panes("/s", out),
                         [{"socket": "/s", "id": "%2", "session": "main", "window": "1", "window_name": "",
                           "cwd": "/home/u/p", "pid": "20"},
                          {"socket": "/s", "id": "%3", "session": "main", "window": "2", "window_name": "backtest",
                           "cwd": "/home/u/p", "pid": "30"}])

    def test_terminal_prompts(self):
        self.assertTrue(events.from_terminal("fix the bug"))
        self.assertFalse(events.from_terminal("<task-notification>done</task-notification>"))
        self.assertFalse(events.from_terminal("/report", ["/report"]))
        self.assertFalse(events.from_terminal('[Cross-session idle notice] "work-2b" is idle now'))
        self.assertTrue(events.from_terminal("[draft] my own note"))
        self.assertEqual(events.quote("a\nb", 10), "> a\n> b")


class GlobalHook(unittest.TestCase):
    """tools/dchook --global: finds its channel by tmux pane; --install edits ~/.claude/settings.json politely."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.makedirs(os.path.join(self.tmp.name, "tools"))
        os.makedirs(os.path.join(self.tmp.name, "state"))
        self.hook = os.path.join(self.tmp.name, "tools", "dchook")
        shutil.copy(os.path.join(ROOT, "tools", "dchook"), self.hook)
        with open(os.path.join(self.tmp.name, "state", "routes.json"), "w") as f:
            json.dump({"/tmp/sock|%7": "55"}, f)

    def tearDown(self):
        self.tmp.cleanup()

    def fire(self, env, ev=None):
        base = dict((k, v) for k, v in os.environ.items() if k not in ("TMUX", "TMUX_PANE", "DCBOT_ROOT", "DCBOT_CHANNEL"))
        r = subprocess.run([sys.executable, self.hook, "--global"], env=dict(base, **env),
                           input=json.dumps(ev or {"hook_event_name": "Stop", "session_id": "s"}).encode(),
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.assertEqual((r.returncode, r.stdout, r.stderr), (0, b"", b""))
        spool = os.path.join(self.tmp.name, "state", "events")
        return events.drain(spool)

    def test_a_pane_number_reused_after_a_tmux_restart_is_not_routed(self):
        with open(os.path.join(self.tmp.name, "state", "routes.json"), "w") as f:
            json.dump({"/tmp/sock|%7": {"channel": "55", "pid": "999999999"}, "/tmp/sock|%9": {"channel": "56", "pid": str(os.getpid())}}, f)
        self.assertEqual(self.fire({"TMUX": "/tmp/sock,123,0", "TMUX_PANE": "%7"}), [], "the attached pane's process is gone")
        got = self.fire({"TMUX": "/tmp/sock,123,0", "TMUX_PANE": "%9"})
        self.assertEqual([e["channel"] for e in got], ["56"], "this test process is an ancestor of the hook it starts")

    def test_a_start_with_no_pane_and_an_id_no_route_names_reaches_no_channel(self):
        """1.6 s after /exit in a viewer of a background conversation, Claude Code starts something of its own: a
        SessionStart with a new id (measured: the connector's hook_payloads/sessions/*/bg.json). Its hook runs with no
        TMUX_PANE, and its id is not the one the viewer's route names: nothing is spooled, so the channel of that pane
        never hears of it and keeps its conversation. (No rule in handle() is needed for it.) The same event with
        the id the route names does arrive: it is the id, not the event, that keeps it out."""
        from altention.tests.connectors.claude_code.test_normalize import SESSIONS, VERSIONS
        for version in VERSIONS:
            with open(os.path.join(SESSIONS, version, "bg.json"), encoding="utf-8") as f:
                run = json.load(f)
            odd = next(ev for ev in run if ev["hook_event_name"] == "SessionStart" and ev.get("agent_type"))
            viewed = run[4]["session_id"]  # the background conversation the viewer shows
            self.assertNotEqual(odd["session_id"], viewed)
            with open(os.path.join(self.tmp.name, "state", "routes.json"), "w") as f:
                json.dump({"/tmp/sock|%7": {"channel": "77", "pid": str(os.getpid()), "session": viewed}}, f)
            self.assertEqual(self.fire({}, odd), [], version)
            self.assertEqual(self.fire({"TMUX": "/tmp/other,1,0"}, odd), [], "nor with a tmux of its own and no pane")
            got = self.fire({}, dict(odd, session_id=viewed))
            self.assertEqual([(e["channel"], e["hook_event_name"]) for e in got], [("77", "SessionStart")])

    def test_routed_pane_reports_to_its_channel(self):
        got = self.fire({"TMUX": "/tmp/sock,123,0", "TMUX_PANE": "%7"})
        self.assertEqual([(e["channel"], e["hook_event_name"]) for e in got], [("55", "Stop")])

    def test_everything_else_stays_silent(self):
        self.assertEqual(self.fire({"TMUX": "/tmp/sock,123,0", "TMUX_PANE": "%8"}), [])   # a pane nobody attached
        self.assertEqual(self.fire({}), [])                                              # not in tmux at all
        self.assertEqual(self.fire({"TMUX": "/tmp/bot,123,0", "TMUX_PANE": "%7", "DCBOT_CHANNEL": "9",
                                    "DCBOT_ROOT": self.tmp.name}), [])  # the bot's own session reports by itself

    def linked(self):
        """A folder reached under two names, as /tmp is on macOS (a link to /private/tmp): (the name the bot wrote,
        the name tmux gives in $TMUX), both of one socket file."""
        real = os.path.join(self.tmp.name, "private-tmp", "tmux-501")
        os.makedirs(real)
        open(os.path.join(real, "default"), "w").close()
        os.symlink(os.path.join(self.tmp.name, "private-tmp"), os.path.join(self.tmp.name, "tmp-link"))
        return os.path.join(self.tmp.name, "tmp-link", "tmux-501", "default"), os.path.join(real, "default")

    def test_a_socket_reached_through_a_link_is_the_same_socket(self):
        """#issues 25 (the Mac's test channel, 2026-10-12): the bot wrote /tmp/tmux-501/default into the table, tmux
        tells the hook /private/tmp/tmux-501/default; the pane found no channel and none of its events were sent
        (the message was typed in and answered, and the bot warned that it might not have arrived)."""
        written, told = self.linked()
        self.assertNotEqual(written, told)
        with open(os.path.join(self.tmp.name, "state", "routes.json"), "w") as f:
            json.dump({"%s|%%8" % written: {"channel": "77", "pid": str(os.getpid())}, "/tmp/sock|%8": "55"}, f)
        got = self.fire({"TMUX": "%s,40404,7" % told, "TMUX_PANE": "%8"})
        self.assertEqual([(e["channel"], e["hook_event_name"]) for e in got], [("77", "Stop")])
        self.assertEqual([e["channel"] for e in self.fire({"TMUX": "%s,40404,7" % written, "TMUX_PANE": "%8"})], ["77"], "under the written name as before")
        self.assertEqual(self.fire({"TMUX": "%s,40404,7" % told, "TMUX_PANE": "%9"}), [], "another pane of that server: not attached")
        with open(os.path.join(self.tmp.name, "state", "routes.json"), "w") as f:
            json.dump({"%s|%%8" % written: {"channel": "77", "pid": "999999999"}}, f)
        self.assertEqual(self.fire({"TMUX": "%s,40404,7" % told, "TMUX_PANE": "%8"}), [], "the pid check holds for it too")

    def test_a_socket_that_is_not_there_or_has_no_sane_path_is_no_route_and_no_failure(self):
        """os.path.realpath raises nothing for a path that does not exist; whatever the names, the hook ends 0 with
        nothing said (fire() holds that): it must never fail a turn of Claude Code."""
        with open(os.path.join(self.tmp.name, "state", "routes.json"), "w") as f:
            json.dump({"/nowhere/at/all|%7": "55", "no bar in this key": "56", "/tmp/sock|%7": {"channel": "58"}}, f)
        self.assertEqual(self.fire({"TMUX": "/gone/too,1,0", "TMUX_PANE": "%7"}), [])
        self.assertEqual([e["channel"] for e in self.fire({"TMUX": "/nowhere/at/../at/all,1,0", "TMUX_PANE": "%7"})], ["55"], "the same path, written longer")
        self.assertEqual([e["channel"] for e in self.fire({"TMUX": "/tmp/sock,1,0", "TMUX_PANE": "%7"})], ["58"])
        self.assertEqual(self.fire({"TMUX": ",1,0", "TMUX_PANE": "%7"}), [])
        with open(os.path.join(self.tmp.name, "state", "routes.json"), "w") as f:
            json.dump(["not", "a", "table"], f)
        self.assertEqual(self.fire({"TMUX": "/tmp/sock,1,0", "TMUX_PANE": "%7"}), [])

    def test_where_no_link_is_on_the_way_nothing_changes(self):
        """Linux: /tmp is a folder, both names are one string. A different server with the same pane number is
        still another pane."""
        self.assertEqual([e["channel"] for e in self.fire({"TMUX": "/tmp/sock,123,0", "TMUX_PANE": "%7"})], ["55"])
        self.assertEqual(self.fire({"TMUX": "/tmp/sock2,123,0", "TMUX_PANE": "%7"}), [])
        self.assertEqual(self.fire({"TMUX": "/private/tmp/sock,123,0", "TMUX_PANE": "%7"}), [],
                         "the hook goes by the file system it runs on: here /private/tmp is no name of /tmp")

    def test_inherited_bot_environment_does_not_hide_a_routed_pane(self):
        got = self.fire({"TMUX": "/tmp/sock,123,0", "TMUX_PANE": "%7", "DCBOT_CHANNEL": "9", "DCBOT_ROOT": "/elsewhere"})
        self.assertEqual([e["channel"] for e in got], ["55"])

    def test_install_keeps_other_hooks_and_is_repeatable(self):
        conf = os.path.join(self.tmp.name, "claude")
        os.makedirs(conf)
        mine = {"theme": "dark", "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "other-tool"}]}]}}
        with open(os.path.join(conf, "settings.json"), "w") as f:
            json.dump(mine, f)
        run = lambda flag: subprocess.run([sys.executable, os.path.join(ROOT, "tools", "dchook"), flag], check=True,  # noqa: E731
                                          env=dict(os.environ, CLAUDE_CONFIG_DIR=conf), stdout=subprocess.DEVNULL)
        def read():
            with open(os.path.join(conf, "settings.json")) as f:
                return json.load(f)
        run("--install")
        once = read()
        run("--install")
        self.assertEqual(read(), once)
        self.assertEqual(sorted(once["hooks"]), sorted(HOOK_EVENTS + ("PostToolUse", "PermissionRequest")))
        self.assertEqual(once["hooks"]["PostToolUse"][-1]["matcher"], "Agent|Task|Workflow")
        self.assertEqual(once["theme"], "dark")
        self.assertEqual(once["hooks"]["Stop"][0], mine["hooks"]["Stop"][0])
        self.assertEqual(len(once["hooks"]["Stop"]), 2)
        asks = [g for g in once["hooks"]["PreToolUse"] if g.get("matcher") == "AskUserQuestion"]
        self.assertEqual(len(asks), 1)
        self.assertIn("--global --ask", asks[0]["hooks"][0]["command"])
        run("--uninstall")
        self.assertEqual(read(), mine)
        run("--update")
        self.assertEqual(read(), mine, "--update refreshes an installation, it does not create one")


@unittest.skipUnless(shutil.which("tmux"), "tmux not installed")
class Discovery(unittest.TestCase):
    def test_a_pane_address_is_typed_into_but_never_killed(self):
        socket = os.path.join(tempfile.gettempdir(), "dcroute-test-%d" % os.getpid())
        run = lambda *a: subprocess.run(["tmux", "-S", socket] + list(a), stdout=subprocess.PIPE,  # noqa: E731
                                        stderr=subprocess.DEVNULL, universal_newlines=True)
        try:
            run("new-session", "-d", "-s", "mine", "-x", "80", "-y", "20", "cat")
            pane = {"socket": socket, "id": run("list-panes", "-a", "-F", "#{pane_id}").stdout.strip()}

            async def go():
                self.assertFalse(await tmuxio.alive(pane), "cat is not Claude: nothing may be typed into it")
                await tmuxio.type_text(pane, "hello; there")
                await tmuxio.keys(pane, "Enter")
                await asyncio.sleep(0.5)
                self.assertEqual((await tmuxio.screen(pane)).split("\n"), ["hello; there", "hello; there"])
                await tmuxio.kill(pane)
                self.assertEqual(run("has-session", "-t", "mine").returncode, 0, "the owner's pane must survive")
            asyncio.run(go())
        finally:
            run("kill-server")
            if os.path.exists(socket):
                os.remove(socket)


if __name__ == "__main__":
    unittest.main()


class MovedToTheBackground(unittest.TestCase):
    """tools/dchook --global and a conversation moved out of its pane (the left key on an empty input box, #issues
    32): its hooks run under Claude Code's daemon, with no pane and an id no route names. The record of the process
    still in the pane names it. The records here bear the fields of the measured ones (2.1.288, 2.1.296) that are
    read; the pane's process is a child of this test, which stands for the process the route was written with."""
    NEW, OLD = "78cb859a-4150-4c2e-b027-033964dcd9b7", "a2511139-65da-48cd-8ea8-449b387b50dd"

    fire, tearDown = GlobalHook.fire, GlobalHook.tearDown

    def setUp(self):
        GlobalHook.setUp(self)  # a copy of the hook in a folder of its own, with a route table
        self.cc = os.path.join(self.tmp.name, "cc")
        os.makedirs(os.path.join(self.cc, "sessions"))
        self.pane = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        self.addCleanup(self.pane.wait)
        self.addCleanup(self.pane.kill)
        self.record(self.pane.pid, sessionId=self.OLD, kind="interactive", tmux="work:@1.%7", parkedJobId=self.NEW[:8])
        self.record(1, sessionId=self.NEW, kind="bg", jobId=self.NEW[:8])
        self.route({"/tmp/sock|%7": {"channel": "55", "pid": str(os.getpid())}})

    def record(self, pid, **more):
        with open(os.path.join(self.cc, "sessions", "%d.json" % pid), "w") as f:
            json.dump(dict({"pid": pid}, **more), f)

    def route(self, table):
        with open(os.path.join(self.tmp.name, "state", "routes.json"), "w") as f:
            json.dump(table, f)

    def moved(self, ev=None, **env):
        engine = os.path.dirname(ROOT)  # the hook under test is a copy in a folder of its own: the connector is found by this
        return self.fire(dict({"CLAUDE_CONFIG_DIR": self.cc, "PYTHONPATH": engine}, **env),
                         ev or {"hook_event_name": "Stop", "session_id": self.NEW, "last_assistant_message": "hi"})

    def test_its_events_reach_the_channel_of_the_pane_it_left_and_say_so(self):
        got = self.moved()
        self.assertEqual([(e["channel"], e["hook_event_name"], e["dc_parked"], e["session_id"]) for e in got], [("55", "Stop", True, self.NEW)])
        self.assertNotIn("dc_pane", got[0], "the route table's events never say a pane")
        start = self.moved({"hook_event_name": "SessionStart", "source": "fork", "session_id": self.NEW})
        self.assertEqual([(e["channel"], e["source"], e["dc_parked"]) for e in start], [("55", "fork", True)], "from the first event on")

    def test_nothing_is_written(self):
        before = sorted(os.listdir(os.path.join(self.tmp.name, "state")))
        with open(os.path.join(self.tmp.name, "state", "routes.json")) as f:
            table = f.read()
        self.assertEqual(len(self.moved()), 1)
        self.assertEqual(sorted(n for n in os.listdir(os.path.join(self.tmp.name, "state")) if n != "events"), before)
        with open(os.path.join(self.tmp.name, "state", "routes.json")) as f:
            self.assertEqual(f.read(), table, "the route table is read, never written (it has no lock: one writer a machine)")

    def test_other_conversations_reach_no_channel(self):
        other = "e27ee4fd-b08e-46bf-8e17-f06f3ddd8ba5"   # the spare process that starts beside it; one typed into the list
        self.record(2, sessionId=other, kind="bg", jobId=other[:8])
        self.assertEqual(self.moved({"hook_event_name": "SessionStart", "source": "startup", "session_id": other}), [])
        self.assertEqual(self.moved({"hook_event_name": "Stop", "session_id": "78cb859b-0000-4000-8000-000000000000"}), [])
        self.assertEqual(self.moved({"hook_event_name": "Stop"}), [], "an event with no id")

    def test_only_an_event_with_no_pane(self):
        """A Claude Code in a pane that is not attached stays silent whatever its id is: only what has no pane at
        all is looked up this way."""
        self.assertEqual(self.moved(TMUX="/tmp/sock,123,0", TMUX_PANE="%8"), [])

    def test_the_process_in_the_pane_has_to_be_there_still(self):
        self.pane.kill()
        self.pane.wait()
        self.assertEqual(self.moved(), [], "the pane's Claude Code is gone: its record is a leftover, the conversation is nobody's")

    def test_the_record_has_to_be_of_the_attached_pane(self):
        self.record(self.pane.pid, sessionId=self.OLD, kind="interactive", tmux="work:@1.%8", parkedJobId=self.NEW[:8])
        self.assertEqual(self.moved(), [], "another pane's number")
        self.record(self.pane.pid, sessionId=self.OLD, kind="interactive", tmux="work:@1.%7", parkedJobId=self.NEW[:8])
        self.route({"/tmp/sock|%7": {"channel": "55", "pid": "1"}})
        self.assertEqual(self.moved(), [], "the same pane number under a process that is not above it (another tmux server's %7)")
        self.route({"/tmp/sock|%7": {"channel": "55", "pid": str(self.pane.pid)}})
        self.assertEqual(len(self.moved()), 1, "the pane's process is Claude Code itself")
        self.route({"/tmp/sock|%7": "55"})
        self.assertEqual(self.moved(), [], "a route of before processes were kept says too little")
        self.record(self.pane.pid, sessionId=self.OLD, kind="interactive", parkedJobId=self.NEW[:8])
        self.route({"/tmp/sock|%7": {"channel": "55", "pid": str(os.getpid())}})
        self.assertEqual(self.moved(), [], "a record that names no pane")

    def test_a_route_by_the_conversation_itself_comes_first_and_is_not_marked(self):
        self.route({"/tmp/sock|%7": {"channel": "55", "pid": str(os.getpid())},
                    "/tmp/sock|%9": {"channel": "66", "pid": "1", "session": self.NEW}})  # a `claude attach` viewer of it
        got = self.moved()
        self.assertEqual([(e["channel"], e.get("dc_parked")) for e in got], [("66", None)])

    def test_a_question_or_a_plan_of_it_is_marked_too(self):
        """The events the hook makes of a question and of a plan are its own, with a few of Claude Code's fields carried
        over: the mark goes with them, or the bot would take the question for a word from the pane's own Claude Code."""
        import importlib.machinery
        import importlib.util
        loader = importlib.machinery.SourceFileLoader("dchook_ours", self.hook)
        mod = importlib.util.module_from_spec(importlib.util.spec_from_loader("dchook_ours", loader))
        loader.exec_module(mod)
        self.assertEqual(mod.ours({"dc_parked": True, "session_id": "s"}, None), {"dc_parked": True})
        self.assertEqual(mod.ours({"session_id": "s"}, {"socket": "/s", "pane": "%1"}), {"dc_pane": {"socket": "/s", "pane": "%1"}})
        self.assertEqual(mod.ours({}, None), {})
        spooled = []
        with mock.patch.object(mod, "spool_event", lambda root, ev: spooled.append(ev)), mock.patch.object(mod, "wait_answer", lambda root, ask_id: None):
            mod.ask(self.tmp.name, "55", {"session_id": self.NEW, "dc_parked": True, "tool_input": {"questions": []}, "tool_use_id": "t"})
            mod.plan(self.tmp.name, "55", {"session_id": self.NEW, "dc_parked": True, "tool_input": {"plan": "p"}})
            mod.ask(self.tmp.name, "55", {"session_id": self.OLD, "tool_input": {}})
        self.assertEqual([(e["hook_event_name"], e.get("dc_parked")) for e in spooled], [("Ask", True), ("Plan", True), ("Ask", None)])

    def test_records_that_cannot_be_read_end_in_silence(self):
        for name, text in (("9.json", "{not json"), ("10.json", "[1]"), ("11.json", json.dumps({"pid": "x", "parkedJobId": self.NEW[:8], "tmux": "a:@1.%7"}))):
            with open(os.path.join(self.cc, "sessions", name), "w") as f:
                f.write(text)
        self.assertEqual(len(self.moved()), 1)
        self.assertEqual(self.moved(CLAUDE_CONFIG_DIR=os.path.join(self.tmp.name, "none")), [], "no such folder")

    def test_a_machine_with_no_pane_attached_reads_no_record(self):
        """Every Claude Code of the machine that is in no pane runs through here: with nothing attached the
        connector is not even loaded (a PYTHONPATH that would break the import shows it was not tried)."""
        self.route({})
        engine = os.path.dirname(ROOT)
        self.assertEqual(self.fire({"CLAUDE_CONFIG_DIR": self.cc, "PYTHONPATH": engine}), [])
        self.route({"/tmp/sock|%7": {"channel": "55", "pid": str(os.getpid())}})
        self.assertEqual(self.fire({"CLAUDE_CONFIG_DIR": self.cc, "PYTHONPATH": os.path.join(self.tmp.name, "none")},
                                   {"hook_event_name": "Stop", "session_id": self.NEW}), [], "no connector to be found: silence, exit 0")


class MovedOutOfTheBotsWindow(unittest.TestCase):
    """The hook the bot hands its own windows (DCBOT_ROOT, DCBOT_CHANNEL; not --global), run by a background process
    of Claude Code's daemon: it has the variables of the window it was started from, and no pane. Measured (2.1.288,
    2.1.296): so have the conversation moved out of the window, the spare process started beside it, and every
    conversation typed into the agents list. Only the first is the window's."""
    NEW, OLD, SPARE = MovedToTheBackground.NEW, MovedToTheBackground.OLD, "e27ee4fd-b08e-46bf-8e17-f06f3ddd8ba5"
    tearDown = GlobalHook.tearDown

    def setUp(self):
        GlobalHook.setUp(self)
        self.cc = os.path.join(self.tmp.name, "cc")
        os.makedirs(os.path.join(self.cc, "sessions"))
        os.remove(os.path.join(self.tmp.name, "state", "routes.json"))  # a window the bot opened is in no route table
        self.pane = self.process({"TMUX": "/tmp/sock,123,0", "TMUX_PANE": "%7"})
        self.record(self.pane.pid, sessionId=self.OLD, kind="interactive", tmux="work:@1.%7", parkedJobId=self.NEW[:8])
        self.record(1, sessionId=self.NEW, kind="bg", jobId=self.NEW[:8])
        self.record(2, sessionId=self.SPARE, kind="bg", jobId=self.SPARE[:8])

    def process(self, env):
        base = dict((k, v) for k, v in os.environ.items() if k not in ("TMUX", "TMUX_PANE"))
        p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"], env=dict(base, **env))
        self.addCleanup(p.wait)
        self.addCleanup(p.kill)
        return p

    record = MovedToTheBackground.record

    def fire(self, session, job=True, name="Stop", **env):
        base = dict((k, v) for k, v in os.environ.items() if k not in ("TMUX", "TMUX_PANE", "DCBOT_ROOT", "DCBOT_CHANNEL", "CLAUDE_JOB_DIR"))
        base.update({"DCBOT_ROOT": self.tmp.name, "DCBOT_CHANNEL": "55", "CLAUDE_CONFIG_DIR": self.cc, "PYTHONPATH": os.path.dirname(ROOT)})
        if job:
            base["CLAUDE_JOB_DIR"] = "/w/jobs/x"
        r = subprocess.run([sys.executable, self.hook], env=dict(base, **env), input=json.dumps({"hook_event_name": name, "session_id": session}).encode(),
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.assertEqual((r.returncode, r.stdout, r.stderr), (0, b"", b""))
        return events.drain(os.path.join(self.tmp.name, "state", "events"))

    def test_the_conversation_moved_comes_as_from_the_window_it_left(self):
        got = self.fire(self.NEW)
        self.assertEqual([(e["channel"], e["dc_pane"], e["dc_parked"], e.get("dc_unplaced")) for e in got],
                         [("55", {"socket": "/tmp/sock", "pane": "%7"}, True, None)])

    def test_what_no_pane_accounts_for_is_passed_on_marked_with_no_pane(self):
        """The spare process, a conversation typed into the list; and the moved conversation once its window's Claude
        Code has ended. The hook cannot tell them apart; the bot can, by the session its channel has."""
        for session in (self.SPARE, "0e51662b-0ecd-4586-a03d-9f813ca51a5e"):
            got = self.fire(session, name="SessionStart")
            self.assertEqual([(e["channel"], e.get("dc_pane"), e.get("dc_parked"), e["dc_unplaced"]) for e in got], [("55", None, None, True)])
        self.pane.kill()
        self.pane.wait()
        got = self.fire(self.NEW)
        self.assertEqual([(e["channel"], e.get("dc_pane"), e.get("dc_parked"), e["dc_unplaced"], e["session_id"]) for e in got],
                         [("55", None, None, True, self.NEW)], "its window gone: no longer placed, still passed on")
        self.assertEqual(sorted(os.listdir(os.path.join(self.tmp.name, "state"))), ["events"], "the hook keeps no log of its own")

    def test_an_event_with_no_pane_that_is_no_background_process_goes_as_it_always_did(self):
        """The variable only decides whether the event is looked into. Without it (a Claude Code outside tmux that has
        the channel's variable; a version that stops setting it) nothing changes: passed on, no pane said."""
        for session in (self.SPARE, self.NEW, "s"):
            got = self.fire(session, job=False)
            self.assertEqual([(e["channel"], e.get("dc_pane"), e.get("dc_parked"), e.get("dc_unplaced")) for e in got], [("55", None, None, None)], session)

    def test_a_windows_own_event_goes_as_it_always_did(self):
        got = self.fire(self.OLD, job=False, TMUX="/tmp/sock,123,0", TMUX_PANE="%7")
        self.assertEqual([(e["dc_pane"], e.get("dc_parked")) for e in got], [({"socket": "/tmp/sock", "pane": "%7"}, None)])
        got = self.fire(self.SPARE, TMUX="/tmp/sock,123,0", TMUX_PANE="%7")
        self.assertEqual(len(got), 1, "in a pane, whatever else its environment holds: the pane says whose it is")

    def test_a_process_whose_pane_cannot_be_read_counts_as_none(self):
        other = self.process({})   # it says the conversation was moved out of it, and its environment names no tmux
        self.record(self.pane.pid, sessionId=self.OLD, kind="interactive", tmux="work:@1.%7")
        self.record(other.pid, sessionId=self.OLD, kind="interactive", tmux="work:@1.%7", parkedJobId=self.NEW[:8])
        got = self.fire(self.NEW)
        self.assertEqual([(e.get("dc_pane"), e.get("dc_parked"), e.get("dc_unplaced")) for e in got], [(None, None, True)])

    def test_a_window_that_was_attached_as_a_pane_since_is_reported_by_the_route_table_alone(self):
        with open(os.path.join(self.tmp.name, "state", "routes.json"), "w") as f:
            json.dump({"/tmp/sock|%7": {"channel": "66", "pid": str(self.pane.pid)}}, f)
        self.assertEqual(self.fire(self.NEW), [], "the global hook reports it, to the channel the table names")

    def test_a_question_of_what_is_not_the_channels_is_left_to_the_terminal(self):
        """The bot answers such a question's hook with {"pass": true}: nothing is printed, so Claude Code shows its
        own menu (a refusal would have the owner's conversation in the list told it cannot ask)."""
        import contextlib
        import importlib.machinery
        import importlib.util
        import io
        loader = importlib.machinery.SourceFileLoader("dchook_pass", self.hook)
        mod = importlib.util.module_from_spec(importlib.util.spec_from_loader("dchook_pass", loader))
        loader.exec_module(mod)
        spooled, out = [], io.StringIO()
        with mock.patch.object(mod, "spool_event", lambda root, ev: spooled.append(ev)), contextlib.redirect_stdout(out):
            with mock.patch.object(mod, "wait_answer", lambda root, ask_id: {"pass": True}):
                mod.ask(self.tmp.name, "55", {"session_id": self.SPARE, "dc_unplaced": True, "tool_input": {"questions": []}})
            self.assertEqual((out.getvalue(), [e.get("dc_unplaced") for e in spooled]), ("", [True]))
            with mock.patch.object(mod, "wait_answer", lambda root, ask_id: {}):
                mod.ask(self.tmp.name, "55", {"session_id": self.SPARE, "tool_input": {}})
            self.assertIn('"deny"', out.getvalue(), "nobody answered: refused with the reason, as before")


class BackgroundSessions(unittest.TestCase):
    def test_a_claude_attach_viewer_routes_by_session_id(self):
        import json as _json
        import subprocess as _sp
        import tempfile as _tf
        from altention.connectors.claude_code import sessions as _t
        home = _tf.mkdtemp()
        os.makedirs(os.path.join(home, ".claude", "sessions"))
        with open(os.path.join(home, ".claude", "sessions", "1.json"), "w") as f:
            _json.dump({"sessionId": "e7b58260-5ae2-4412-9e6f-e505208c2b16", "kind": "bg"}, f)
        viewer = _sp.Popen([sys.executable, "-c", "import time; time.sleep(30)", "attach", "e7b58260"])
        try:
            import time as _time
            for _ in range(50):  # the child shows its arguments once it has started
                with mock.patch.dict(os.environ, {"HOME": home, "CLAUDE_CONFIG_DIR": ""}):
                    got = _t.attached_session(viewer.pid)
                if got:
                    break
                _time.sleep(0.1)
            self.assertEqual(got, "e7b58260-5ae2-4412-9e6f-e505208c2b16")
        finally:
            viewer.kill()
        root = _tf.mkdtemp()
        os.makedirs(os.path.join(root, "state"))
        with open(os.path.join(root, "state", "routes.json"), "w") as f:
            _json.dump({"/tmp/s|%4": {"channel": "99", "pid": "1", "session": "e7b58260-5ae2-4412-9e6f-e505208c2b16"}}, f)
        dchook = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools", "dchook")
        ev = {"hook_event_name": "Stop", "session_id": "e7b58260-5ae2-4412-9e6f-e505208c2b16", "last_assistant_message": "hi"}
        import importlib.machinery
        import importlib.util
        loader = importlib.machinery.SourceFileLoader("dchook_mod", dchook)
        spec = importlib.util.spec_from_loader("dchook_mod", loader)
        mod = importlib.util.module_from_spec(spec)
        loader.exec_module(mod)
        self.assertEqual(mod.routed_session(root, ev["session_id"]), "99")
        self.assertIsNone(mod.routed_session(root, "other"))


class Clip(unittest.TestCase):
    def test_a_long_reply_says_it_was_cut(self):
        import importlib.machinery
        import importlib.util
        path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools", "dchook")
        loader = importlib.machinery.SourceFileLoader("dchook_clip", path)
        mod = importlib.util.module_from_spec(importlib.util.spec_from_loader("dchook_clip", loader))
        loader.exec_module(mod)
        self.assertEqual(mod.clip("short"), "short")
        cut = mod.clip("x" * (mod.MAX_TEXT + 5))
        self.assertTrue(cut.startswith("x" * mod.MAX_TEXT))
        self.assertIn("已截斷", cut)
        self.assertIn(str(mod.MAX_TEXT + 5), cut)
