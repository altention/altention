"""C-148 batch 1: golden files of the CLI as other programs use it (spec §11): -h/--help and wrong usage of the
commands that have them, and the --json answers' forms (keys, types and fixed values; times and ids aside). Run in
the throwaway home. A change here is a change of interface: tools/golden update <case>, and a "golden:" line in the
commit (tools/golden --help)."""
import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))))
from altention import testing as _testing  # noqa: E402
_testing.guard_home()  # tests never touch the real home (altention/testing.py)
import contextlib
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from altention import testing
from ops import plugins

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import doctor  # noqa: E402

ENGINE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# §11.1: every command takes -h/--help (stdout, nothing done, 0); wrong usage is 2 (C-150: all of chapter 11's and the
# cron entries; a new command joins here, and the file says it). A .py is run by this Python; "-m x" as a module.
HELP = (["bin/altention", "--help"], ["bin/altention", "-h"], ["bin/altention", "dc", "post", "--help"],
        ["bin/altention", "dc", "read", "--help"], ["bin/altention", "dc", "notify", "--help"], ["bin/home-git", "--help"],
        ["dcbot/tools/dcctl", "--help"], ["dcbot/tools/dcremote", "--help"], ["tools/lint", "--help"],
        ["tools/golden", "--help"],
        ["bin/altention", "doctor", "--help"], ["bin/altention", "apply", "--help"], ["bin/altention", "plugins", "--help"],
        ["bin/altention", "plugins", "show", "--help"], ["bin/altention", "update", "--help"],
        ["ops/doctor.py", "--help"], ["ops/apply.py", "-h"], ["ops/plugins.py", "list", "--help"], ["ops/instance.py", "--help"],
        ["ops/remote.py", "--help"], ["ops/release.py", "check", "--help"], ["ops/setup.py", "--help"],
        ["ops/setup.py", "answer", "--help"], ["altention/update.py", "--help"], ["altention/update_go.py", "go", "--help"],
        ["-m", "altention.sync", "--help"], ["usage/spend.py", "--json", "--help"], ["install.sh", "--help"],
        ["bin/home-pull", "--help"], ["tools/privacy-scan", "--help"], ["planner/remind.py", "--help"],
        ["altention/cron_update.py", "--help"], ["altention/cron_sync.py", "--help"], ["altention/cron_schedule.py", "--help"],
        ["usage/collect.py", "--help"], ["status/boards.py", "--help"])
WRONG = (["bin/altention", "no-such-command"], ["bin/altention", "dc", "no-such-command"], ["tools/golden", "no-such"],
         ["altention/cron_update.py", "--now"], ["altention/cron_sync.py", "x"], ["altention/cron_schedule.py", "--nope"],
         ["usage/collect.py", "--nope"], ["status/boards.py", "--nope"], ["bin/home-pull", "--nope"])


def load(name):
    spec = importlib.util.spec_from_file_location("usage_" + name, os.path.join(ENGINE, "usage", name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class Help(unittest.TestCase):
    def run_cli(self, argv):
        """argv run in a home of its own (the suite's throwaway home holds other tests' files): (exit, stdout)."""
        home = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, home, True)
        cmd = [sys.executable] + argv if argv[0] == "-m" else [sys.executable, os.path.join(ENGINE, argv[0])] + argv[1:] \
            if argv[0].endswith(".py") else [os.path.join(ENGINE, argv[0])] + argv[1:]
        p = subprocess.run(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE, universal_newlines=True, timeout=60, cwd=ENGINE,
                           env=dict(os.environ, HOME=home, XDG_STATE_HOME=os.path.join(home, ".local", "state"),
                                    XDG_CACHE_HOME=os.path.join(home, ".cache"), XDG_CONFIG_HOME=os.path.join(home, ".config"),
                                    XDG_DATA_HOME=os.path.join(home, ".local", "share"), ALT_INSTANCE=os.path.join(home, "none")))
        self.assertEqual(os.listdir(home), [], "%s made nothing in the home" % " ".join(argv))
        return p.returncode, p.stdout

    def test_help_and_wrong_usage(self):
        rows = []
        for argv in HELP:
            code, out = self.run_cli(argv)
            rows.append("%s → %d, %s\n" % (" ".join(argv), code, "說明在 stdout" if out.strip() else "stdout 沒有東西"))
        for argv in WRONG:
            code, _ = self.run_cli(argv)
            rows.append("%s → %d\n" % (" ".join(argv), code))
        testing.golden("cli/help", "".join(rows), "§11.1")


class Spend(unittest.TestCase):
    """usage/spend.py --json, v1 and v2 (§11, usage.spend): the forms Activity reads."""
    def answer(self, *more):
        collect, spend = load("collect"), load("spend")
        collect.save_spend([{"machine": "lap", "sessions": [{"id": "s1", "agent": "claude", "cwd": "/w/a", "project": "a",
                                                             "cost1": 1.0, "cost7": 2.0, "cost30": 3.0, "costAll": 4.0,
                                                             "tokens1": 1, "tokens7": 2, "tokens30": 3, "tokensAll": 4,
                                                             "last": "2026-10-24"}],
                             "since": "2026-10-01", "daily": [{"date": "2026-10-24", "agent": "claude", "cost": 1.0}]},
                            {"machine": "box", "error": "連不上"}], "2026-10-24", now=1000.0)
        with open(spend.samples_path(), "w") as f:
            f.write(json.dumps({"t": 1792800000.0, "limits": [["Current week (all models)", 40, "x"], ["Current session", 7, "y"]],
                                "resets": {"seven_day": 9, "five_hour": 8}}) + "\n")
        out = io.StringIO()
        with mock.patch.object(spend, "bot_state", lambda: os.path.join(os.environ["HOME"], "none")), \
                mock.patch.object(spend, "limits_off", lambda: False), \
                mock.patch.object(spend, "plan", lambda: {"name": "Max", "pro": 5}), \
                mock.patch.object(spend.time, "time", lambda: 1792800100.0), contextlib.redirect_stdout(out):
            self.assertEqual(spend.main(["--json"] + list(more)), 0)
        return json.loads(out.getvalue())

    def test_v1(self):
        testing.golden("cli/spend-v1", testing.shape(self.answer(), keep=("v", "windows", "limits_why")), "§11, usage.spend v1")

    def test_v2(self):
        testing.golden("cli/spend-v2", testing.shape(self.answer("--v", "2"), keep=("v", "windows", "limits_why", "days_why")),
                       "§11, usage.spend v2")


class PluginsJson(unittest.TestCase):
    def test_show_json(self):
        rows = [("demo", "/r/plugins/demo", {}, "on", ""), ("rig", "/r/plugins/rig", {}, "blocked", "它需要的介面 x，這版引擎沒有")]
        out = io.StringIO()
        with mock.patch.object(plugins.plugin, "this_machine", return_value="lap"), \
                mock.patch.object(plugins.config, "inventory", return_value={"machines": {"lap": {}}}), \
                mock.patch.object(plugins, "placements", return_value=(rows, [], [])):
            plugins.show("lap", as_json=True, out=out)
        testing.golden("cli/plugins-show", testing.shape(json.loads(out.getvalue()), keep=("placement",)), "§11.8")

    def test_uses_json(self):
        got = ("on", {"plugin.format": {"ok": True, "optional": False},
                      "rig.spend": {"ok": False, "optional": True, "why": "rig.spend 由插件 rigp 提供，但 rigp 在這台沒開"}})
        out = io.StringIO()
        with mock.patch.object(plugins.plugin, "uses_now", return_value=got), mock.patch("sys.stdout", out):
            self.assertEqual(plugins.main(["uses", "demo", "--json"]), 0)
        testing.golden("cli/plugins-uses", testing.shape(json.loads(out.getvalue()), keep=("where",)), "§11.8, plugin.format v2")


class DoctorJson(unittest.TestCase):
    def test_records(self):
        """§11.6: doctor --json is a list of whole records; status is one of four words."""
        recs = [doctor.record("core.reachable", "lap", "fail", "連不上", "ssh lap"), doctor.record("core.sync", "lap", "ok", ""),
                doctor.record("core.cron", None, "warn", "x", "y", value={"n": 1})]
        out = io.StringIO()
        with mock.patch.object(doctor, "collect", return_value=recs), contextlib.redirect_stdout(out):
            self.assertEqual(doctor.main(["--json"]), 1)
        got = json.loads(out.getvalue())
        self.assertEqual(sorted(set(r["status"] for r in got)), ["fail", "ok", "warn"])
        testing.golden("cli/doctor-json", testing.shape(got), "§11.6")


if __name__ == "__main__":
    unittest.main()
