"""Sessions on other machines: addresses, routing, the machine list, and dcremote on its own."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

from bot import archive, remote, routes, tmuxio
from tests import tmuxguard  # noqa: F401,E402  (no test reaches a real tmux server)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
P = {"socket": "/tmp/tmux-1/default", "id": "%34", "session": "slr", "window": "0", "window_name": "", "cwd": "/w/project-a",
     "pid": "77", "host": "HostB", "project": "project-a", "routed": None}


class Addresses(unittest.TestCase):
    def test_keys_labels_and_routing(self):
        self.assertEqual(routes.key(P), "HostB|/tmp/tmux-1/default|%34")
        self.assertEqual(routes.key(dict(P, host=None)), "/tmp/tmux-1/default|%34")
        self.assertEqual(routes.window(P), "HostB · slr:0")
        self.assertIsNone(routes.routed_to(P, {"/tmp/tmux-1/default|%34": "9"}), "a remote pane's route is its own machine's")
        self.assertEqual(routes.routed_to(dict(P, routed="5"), {}), "5")
        self.assertEqual(routes.project_of(P), "project-a", "worked out on that machine")
        self.assertEqual(list(routes.by_project([P, dict(P, id="%35")], {})), ["project-a"])
        self.assertEqual(tmuxio.on(P), {"socket": "/tmp/tmux-1/default", "host": "HostB"})
        self.assertEqual(tmuxio.on("dc-x"), {})
        self.assertIn("HostB 上的", tmuxio.label(P))
        self.assertFalse(archive.same_pane(P, dict(P, host=None)), "same numbers on two machines are two panes")

    def test_machines_with_the_role(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "machines.json")
            with open(path, "w") as f:
                json.dump({"machines": {"A": {"ssh": ["A", "AP"], "roles": ["dcattach"]}, "B": {"ssh": ["local"], "roles": ["dcattach"]},
                                        "C": {"ssh": ["C"], "roles": ["chatlog"]}}}, f)
            with open(path) as f:
                data = json.load(f)
            from unittest import mock
            with mock.patch.object(remote, "inventory", return_value=data), mock.patch.object(remote, "this_machine", return_value="Z"):
                self.assertEqual(remote.hosts(), {"A": "A"}, "no SSH alias, or not the role: not attachable")
                self.assertEqual(remote.argv("A", "true")[-2:], ["A", remote.PATH_FIX + "true"])
            with mock.patch.object(remote, "inventory", return_value={}):
                self.assertEqual(remote.hosts(), {}, "no machine list: one machine, nothing to attach from")


class Dcremote(unittest.TestCase):
    """tools/dcremote run like the bot runs it over SSH, against a throwaway copy of dcbot."""

    def setUp(self):
        """A copy laid out as an engine is: dcbot/ with the bot and the tool, and beside it altention/ with the
        connectors the bot's modules import (stage 3b: without it the tool does not start at all)."""
        self.tmp = tempfile.TemporaryDirectory()
        engine = os.path.dirname(ROOT)
        shutil.copytree(os.path.join(ROOT, "bot"), os.path.join(self.tmp.name, "dcbot", "bot"))
        os.makedirs(os.path.join(self.tmp.name, "dcbot", "tools"))
        self.tool = os.path.join(self.tmp.name, "dcbot", "tools", "dcremote")
        shutil.copy(os.path.join(ROOT, "tools", "dcremote"), self.tool)
        os.makedirs(os.path.join(self.tmp.name, "altention"))
        shutil.copy(os.path.join(engine, "altention", "__init__.py"), os.path.join(self.tmp.name, "altention", "__init__.py"))
        shutil.copytree(os.path.join(engine, "altention", "connectors"), os.path.join(self.tmp.name, "altention", "connectors"))

        # tmux's socket folder and the bot's settings file: this test's own (empty, none), not the owner's servers
        self.apart = {"TMUX_TMPDIR": os.path.join(self.tmp.name, "sockets"), "DCBOT_ENV": os.path.join(self.tmp.name, "no-settings")}
        os.makedirs(self.apart["TMUX_TMPDIR"])
        self.env = dict(os.environ, **self.apart)

    def tearDown(self):
        self.tmp.cleanup()

    def run_tool(self, *args, input=None):
        r = subprocess.run(["/usr/bin/python3", self.tool] + list(args), input=input, env=self.env, stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE, universal_newlines=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout

    def test_the_real_tool_starts_with_the_system_python_from_anywhere(self):
        """As the bot starts it over SSH on another machine: the system Python, some other working directory,
        nothing of this environment. An import it cannot find there (the connectors lie in the engine, beside
        dcbot/) would end that machine's stream at once and leave its channels silent, where no rollback reaches."""
        clean = dict(self.apart, PATH="/usr/bin:/bin", HOME=self.tmp.name, LANG="C.UTF-8")
        for args in (["--help"], ["panes"]):
            r = subprocess.run(["/usr/bin/python3", os.path.join(ROOT, "tools", "dcremote")] + args, cwd=self.tmp.name, env=clean,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, timeout=30)
            self.assertEqual((r.returncode, "Traceback" in r.stderr), (0, False), (args, r.stderr[-400:]))
        self.assertIsInstance(json.loads(r.stdout), list)

    def test_the_real_hook_installs_with_the_system_python_from_anywhere(self):
        """`dchook --install` (and --update, which every machine's stream runs first) takes the hook set from the
        connector since stage 3b step 1.5: the engine's folder, not only dcbot/, has to be found. A throwaway
        HOME: the settings file it writes is that home's."""
        clean = {"PATH": "/usr/bin:/bin", "HOME": self.tmp.name, "LANG": "C.UTF-8"}
        hook = os.path.join(ROOT, "tools", "dchook")
        for flag in ("--install", "--update", "--install"):
            r = subprocess.run(["/usr/bin/python3", hook, flag], cwd=self.tmp.name, env=clean,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, timeout=30)
            self.assertEqual((r.returncode, r.stderr), (0, ""), flag)
        with open(os.path.join(self.tmp.name, ".claude", "settings.json")) as f:
            hooks = json.load(f)["hooks"]
        self.assertEqual(len(hooks), 11)
        self.assertEqual([len(groups) for ev, groups in sorted(hooks.items()) if ev == "PreToolUse"], [3], "installed once, not thrice (the events, AskUserQuestion, the secrets guard)")
        self.assertTrue(all(hook in h["command"] for groups in hooks.values() for g in groups for h in g["hooks"]))

    def test_without_the_engine_beside_it_the_tool_says_so_at_once(self):
        """Not a way it is installed; pinned so that the failure is this plain one, not a half-working tool."""
        shutil.rmtree(os.path.join(self.tmp.name, "altention"))
        r = subprocess.run(["/usr/bin/python3", self.tool, "panes"], env=self.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           universal_newlines=True, timeout=30)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("altention", r.stderr)

    def test_bind_unbind_answer(self):
        self.run_tool("bind", "/s|%1", "55", "123")
        self.run_tool("bind", "/s|%2", "66")
        routes_file = os.path.join(self.tmp.name, "dcbot", "state", "routes.json")
        with open(routes_file) as f:
            self.assertEqual(json.load(f), {"/s|%1": {"channel": "55", "pid": "123"}, "/s|%2": {"channel": "66", "pid": ""}})
        self.run_tool("unbind", "55")
        with open(routes_file) as f:
            self.assertEqual(list(json.load(f)), ["/s|%2"])
        self.run_tool("answer", "9-1", input='{"approve": true}')
        with open(os.path.join(self.tmp.name, "dcbot", "state", "answers", "9-1.json")) as f:
            self.assertEqual(json.load(f), {"approve": True})

    def test_panes_is_json(self):
        self.assertEqual(json.loads(self.run_tool("panes")), [], "its own, empty socket folder: no server is asked")


if __name__ == "__main__":
    unittest.main()


class Aliases(unittest.TestCase):
    """A machine reached by its aliases in the list's order, the one that last worked first (2026-10-07: a laptop away
    from the lab answers only on a reverse tunnel listed third). With a fake ssh first in PATH."""
    FAKE = """#!/bin/sh
# the last two words: the alias and the command; CALLS gets "alias" for a command, "alias:true" for a probe
for a in "$@"; do alias="$cmd"; cmd="$a"; done
case "$cmd" in *"; true") echo "$alias:true" >> "$CALLS" ;; *) echo "$alias" >> "$CALLS" ;; esac
case " $UP " in *" $alias "*) ;; *) exit 255 ;; esac
case " $HANG " in *" $alias "*) exec sleep 30 ;; esac
sh -c "$cmd"
rc=$?
case "$cmd $DROP " in *"; true "*) ;; *" $alias "*) exit 255 ;; esac
exit $rc
"""

    def setUp(self):
        import asyncio
        from unittest import mock
        self.asyncio = asyncio
        self.tmp = tempfile.mkdtemp()
        b = os.path.join(self.tmp, "bin")
        os.makedirs(b)
        with open(os.path.join(b, "ssh"), "w") as f:
            f.write(self.FAKE)
        os.chmod(os.path.join(b, "ssh"), 0o755)
        self.calls = os.path.join(self.tmp, "calls")
        self.env = mock.patch.dict(os.environ, {"PATH": b + os.pathsep + os.environ["PATH"], "CALLS": self.calls,
                                                "UP": "", "HANG": "", "DROP": ""})
        self.env.start()
        inv = {"machines": {"lap": {"ssh": ["LAP", "LAP-LAN", "LAPT"], "roles": ["dcattach"]}}}
        self.inv = mock.patch.object(remote, "inventory", return_value=inv)
        self.inv.start()
        remote.LAST_GOOD.clear()
        remote.TRIED.clear()
        self.problem, remote.problem = remote.problem, None  # the list as readable (other tests may leave it broken)

    def tearDown(self):
        remote.problem = self.problem
        self.inv.stop()
        self.env.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def called(self):
        try:
            with open(self.calls) as f:
                got = f.read().split()
        except OSError:
            got = []
        open(self.calls, "w").close()
        return got

    def run_(self, cmd="echo hi", timeout=30):
        return self.asyncio.run(remote.run("lap", cmd, timeout=timeout))

    def test_the_first_down_the_third_up_then_tried_first(self):
        os.environ["UP"] = "LAPT"
        self.assertEqual(self.run_(), (0, "hi\n"))
        self.assertEqual(self.called(), ["LAP:true", "LAP-LAN:true", "LAPT:true", "LAPT"], "the way found by true, the command once")
        self.assertEqual(self.run_(), (0, "hi\n"))
        self.assertEqual(self.called(), ["LAPT"], "the one that worked is used straight away: no waiting on the others")
        self.assertEqual(remote.hosts(), {"lap": "LAPT"})

    def test_the_last_good_one_down_the_command_is_not_resent_the_next_finds_the_way(self):
        os.environ["UP"] = "LAPT"
        self.run_()
        self.called()
        os.environ["UP"] = "LAP"  # back in the lab, the tunnel closed
        self.assertEqual(self.run_(), (255, ""))
        self.assertEqual(self.called(), ["LAPT"], "a 255 may come after the command ran: never sent again")
        self.assertNotIn("lap", remote.LAST_GOOD)
        self.assertEqual(self.run_(), (0, "hi\n"))
        self.assertEqual(self.called(), ["LAP:true", "LAP"], "one more true, the command still once")
        self.assertEqual(remote.LAST_GOOD["lap"], "LAP")

    def test_a_command_that_ran_and_then_got_255_runs_once(self):
        ran = os.path.join(self.tmp, "ran")
        os.environ["UP"] = "LAP LAPT"
        os.environ["DROP"] = "LAP"  # the line drops after the command ran
        self.assertEqual(self.run_("echo x >> '%s'" % ran), (255, ""))
        with open(ran) as f:
            self.assertEqual(f.read(), "x\n", "ran exactly once")
        self.assertEqual(self.called(), ["LAP:true", "LAP"])
        self.assertNotIn("lap", remote.LAST_GOOD)

    def test_none_answers_the_message_names_every_alias_tried(self):
        self.assertEqual(self.run_(), (255, ""))
        self.assertEqual(self.called(), ["LAP:true", "LAP-LAN:true", "LAPT:true"])
        self.assertEqual(remote.unreachable("lap"), "連不上 lap（試過 LAP、LAP-LAN、LAPT）")

    def test_the_message_names_only_what_was_tried(self):
        self.assertEqual(remote.unreachable("lap"), "連不上 lap")
        os.environ["UP"] = "LAPT"
        self.run_()
        os.environ["UP"] = ""
        self.run_()
        self.assertEqual(remote.unreachable("lap"), "連不上 lap（試過 LAPT）")

    def test_a_command_that_fails_by_itself_is_not_tried_another_way(self):
        os.environ["UP"] = "LAP LAPT"
        self.assertEqual(self.run_("exit 3"), (3, ""))
        self.assertEqual(self.called(), ["LAP:true", "LAP"])
        self.assertEqual(remote.LAST_GOOD["lap"], "LAP", "the way was fine")

    def test_a_command_that_takes_too_long_is_not_run_again_and_the_way_is_found_afresh(self):
        os.environ["UP"] = "LAP LAPT"
        self.run_()
        self.called()
        os.environ["HANG"] = "LAP"
        self.assertEqual(self.run_(timeout=1), (255, ""))
        self.assertEqual(self.called(), ["LAP"], "maybe a keystroke: never sent twice")
        self.assertNotIn("lap", remote.LAST_GOOD)

    def test_an_alias_taken_out_of_the_list_is_not_used_again(self):
        os.environ["UP"] = "LAP LAPT"
        remote.LAST_GOOD["lap"] = "OLD"  # worked before the list changed
        self.assertEqual(self.run_(), (0, "hi\n"))
        self.assertEqual(self.called(), ["LAP:true", "LAP"], "found afresh from the list")

    def test_an_unreadable_list_keeps_the_remembered_alias(self):
        from unittest import mock
        os.environ["UP"] = "LAPT"
        remote.LAST_GOOD["lap"] = "LAPT"

        def broken():
            remote.broken(ValueError("machines.json cut short"))
            return {}
        with mock.patch.object(remote, "inventory", broken):
            self.assertEqual(self.run_(), (0, "hi\n"))
        self.assertEqual(self.called(), ["LAPT"], "the way that works, while the list is being fixed")
        remote.problem = None

    def test_reach_for_the_stream_skips_a_hanging_alias(self):
        os.environ["UP"] = "LAP LAPT"
        os.environ["HANG"] = "LAP"
        self.assertEqual(self.asyncio.run(remote.reach("lap", timeout=1)), "LAPT")
        self.assertEqual(self.called(), ["LAP:true", "LAP-LAN:true", "LAPT:true"])
        self.assertEqual(remote.argv("lap", "x")[-2], "LAPT", "the stream then connects that way")
        os.environ["UP"] = ""
        self.assertIsNone(self.asyncio.run(remote.reach("lap", timeout=1)))
        self.assertIn("試過 LAPT、LAP、LAP-LAN", remote.unreachable("lap"))
