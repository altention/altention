"""K-04: the subscription's limits come from the rate_limit_event of one `claude -p` request (measured on 2.1.299,
samples/), not from the /usage panel: the numbers and reset times as the samples and boards keep them, a shape not as
measured said as such and never guessed, and the request made with nothing of the owner's loaded or kept, by a
background Claude (altention/background.py). The usage plugin has no tests of its own that a deploy runs: these are
here (they replace test_usage_background.py, which pinned the old tmux probe)."""
import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))))
from altention import testing as _testing  # noqa: E402
_testing.guard_home()  # tests never touch the real home (altention/testing.py)
import datetime
import importlib.util
import io
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

ENGINE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
spec = importlib.util.spec_from_file_location("usage_collect", os.path.join(ENGINE, "usage", "collect.py"))
collect = importlib.util.module_from_spec(spec)
spec.loader.exec_module(collect)
SAMPLE = os.path.join(ENGINE, "usage", "samples", "2.1.299-stream.jsonl")
NOW = datetime.datetime(2026, 10, 14, 16, 40)  # the day the sample was taken (local time; TZ is UTC here)


def setUpModule():
    old = os.environ.get("TZ")
    os.environ["TZ"] = "UTC"
    time.tzset()

    def back():
        if old is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = old
        time.tzset()
    unittest.addModuleCleanup(back)


def sample():
    with open(SAMPLE, encoding="utf-8") as f:
        return f.read()


def with_info(change):
    """The sample with its rate_limit_info changed by change(info)."""
    lines = []
    for line in sample().split("\n"):
        if '"rate_limit_event"' in line:
            d = json.loads(line)
            change(d["rate_limit_info"])
            line = json.dumps(d)
        lines.append(line)
    return "\n".join(lines)


class FromTheStream(unittest.TestCase):
    def test_the_measured_sample(self):
        limits, why = collect.limits_from_stream(sample(), NOW)
        self.assertIsNone(why)
        # 1791975600 = 2026-10-14 11:00 UTC (today: time only), 1792357200 = 2026-10-18 21:00 UTC (always a date)
        self.assertEqual(limits, [["Current session", 16, "11am"], ["Current week (all models)", 40, "Oct 18, 9pm"]])

    def test_boards_read_it_as_they_read_the_panel(self):
        limits, why = collect.limits_from_stream(sample(), NOW)
        board = collect.limits_board(limits, why)
        self.assertIn("這 5 小時：**16%**", board)
        self.assertIn("這週（所有模型）：**40%**　（Oct 18, 9pm 重置）", board)
        old = {"week": 30, "week_reset": "Oct 18, 8:59pm", "cost7": 1.0}  # a sample from the panel, same week
        new = {"week": 40, "week_reset": limits[1][2], "cost7": 2.0}
        self.assertEqual(collect.weeks_board([old, new]).count("到 Oct 18 重置的那週"), 1, "one row for one week")

    def test_reset_texts(self):
        at = lambda *a: datetime.datetime(*a).replace(tzinfo=datetime.timezone.utc).timestamp()
        self.assertEqual(collect.reset_text(at(2026, 10, 14, 0, 30), NOW, False), "12:30am")
        self.assertEqual(collect.reset_text(at(2026, 10, 14, 12, 0), NOW, False), "12pm")
        self.assertEqual(collect.reset_text(at(2026, 10, 15, 4, 59), NOW, False), "Oct 15, 4:59am")
        self.assertEqual(collect.reset_text(at(2026, 10, 14, 23, 0), NOW, True), "Oct 14, 11pm")

    def test_percent_rounds_like_the_panel(self):
        def set_u(i):
            i["unifiedWindows"]["five_hour"]["utilization"] = 0.125
            i["unifiedWindows"]["seven_day"]["utilization"] = 1
        limits, why = collect.limits_from_stream(with_info(set_u), NOW)
        self.assertEqual(([x[1] for x in limits], why), ([12, 100], None))

    def test_over_the_limit_is_shown_as_it_is(self):  # overage, or just past it: real data, not a changed shape
        limits, why = collect.limits_from_stream(
            with_info(lambda i: i["unifiedWindows"]["five_hour"].update(utilization=1.05)), NOW)
        self.assertEqual((limits[0][1], why), (105, None))
        self.assertIn("**105%**", collect.limits_board(limits, why))

    def test_used_up_without_numbers_is_said_as_that(self):
        def rejected(i):
            i["status"] = "rejected"
            i.pop("unifiedWindows")
        limits, why = collect.limits_from_stream(with_info(rejected), NOW)
        self.assertEqual(limits, [])
        self.assertTrue(why.startswith("額度用完了"), why)

    def test_the_last_event_counts(self):
        first = sample().split("\n")[2]
        later = with_info(lambda i: i["unifiedWindows"]["five_hour"].update(utilization=0.5))
        limits, _ = collect.limits_from_stream(first + "\n" + later, NOW)
        self.assertEqual(limits[0][1], 50)
        bare = with_info(lambda i: i.pop("unifiedWindows")).split("\n")[2]  # a later one without the windows
        limits, why = collect.limits_from_stream(later + "\n" + bare, NOW)
        self.assertEqual((limits[0][1], why), (50, None))

    def test_a_shape_not_as_measured_is_said(self):
        cases = {
            "no event": "\n".join(l for l in sample().split("\n") if "rate_limit_event" not in l),
            "no unifiedWindows": with_info(lambda i: i.pop("unifiedWindows")),
            "renamed window": with_info(lambda i: i["unifiedWindows"].pop("seven_day")),
            "percent as text": with_info(lambda i: i["unifiedWindows"]["five_hour"].update(utilization="13%")),
            "percent below 0": with_info(lambda i: i["unifiedWindows"]["five_hour"].update(utilization=-0.1)),
            "no reset time": with_info(lambda i: i["unifiedWindows"]["seven_day"].pop("resetsAt")),
            "bool": with_info(lambda i: i["unifiedWindows"]["seven_day"].update(resetsAt=True)),
            "not an object": with_info(lambda i: i.update(unifiedWindows=[1, 2])),
            "nothing": "",
            "not JSON": "Error: something\n",
        }
        for name, text in cases.items():
            limits, why = collect.limits_from_stream(text, NOW)
            self.assertEqual(limits, [], name)
            self.assertTrue(why.startswith(collect.CHANGED), (name, why))
            self.assertIn("讀不到用量額度：格式變了", collect.limits_board(limits, why), name)

    def test_a_failed_request_is_said_as_that(self):
        text = '{"type": "system", "subtype": "init"}\n{"type": "result", "subtype": "error_during_execution", "is_error": true, "result": "API Error: 529"}\n'
        self.assertEqual(collect.limits_from_stream(text, NOW), ([], "請求失敗：API Error: 529"))


class TheRequest(unittest.TestCase):
    """read_limits with a stand-in claude that writes down how it was run and answers with the sample."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="usage probe 用量 ")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.seen = os.path.join(self.tmp, "seen.json")
        self.claude = os.path.join(self.tmp, "claude")

    def stand_in(self, body):
        with open(self.claude, "w") as f:
            f.write("#!/usr/bin/env python3\nimport json, os, sys\n"
                    "json.dump({'argv': sys.argv[1:], 'cwd': os.getcwd(), 'files': os.listdir('.'), "
                    "'bg': os.environ.get('ALTENTION_BACKGROUND'), 'kept': os.environ.get('KEPT'), 'stdin': sys.stdin.read()}, open(%r, 'w'))\n"
                    % self.seen + body)
        os.chmod(self.claude, stat.S_IRWXU)

    def run_it(self):
        with mock.patch.object(collect, "CLAUDE", self.claude), mock.patch.dict(os.environ, {"KEPT": "1"}):
            os.environ.pop("ALTENTION_BACKGROUND", None)
            got = collect.read_limits()
            self.assertNotIn("ALTENTION_BACKGROUND", os.environ, "the mark is that Claude's, not the collector's own")
        return got

    def test_how_it_is_run(self):
        self.stand_in("sys.stdout.write(open(%r).read())\n" % SAMPLE)
        limits, why = self.run_it()
        self.assertEqual(([x[1] for x in limits], why), ([16, 40], None))
        with open(self.seen) as f:
            seen = json.load(f)
        argv = seen["argv"]
        self.assertEqual(argv[-1], collect.PROMPT)
        for flag in ("-p", "--no-session-persistence", "--strict-mcp-config"):
            self.assertIn(flag, argv)
        for flag, value in (("--model", "haiku"), ("--setting-sources", "project"), ("--tools", ""),
                            ("--output-format", "stream-json")):
            self.assertEqual(argv[argv.index(flag) + 1], value, flag)
        self.assertEqual((seen["files"], seen["stdin"], seen["bg"], seen["kept"]), ([], "", "1", "1"),
                         "an empty folder, no input, marked, the rest of the environment as it was")
        self.assertFalse(os.path.exists(seen["cwd"]), "its folder is removed")
        self.assertTrue(os.path.basename(seen["cwd"]).startswith("usage-probe-"))

    def test_exit_code_and_stderr_said(self):
        self.stand_in("sys.stderr.write('Invalid API key\\n'); sys.exit(1)\n")
        limits, why = self.run_it()
        self.assertEqual(limits, [])
        self.assertIn("結束碼 1：Invalid API key", why)

    def test_too_long(self):
        self.stand_in("import time; time.sleep(30)\n")
        with mock.patch.object(collect, "PROBE_SECS", 1):
            limits, why = self.run_it()
        self.assertEqual((limits, why), ([], "claude 沒有在 1 秒內結束"))
        with open(self.seen) as f:
            self.assertFalse(os.path.exists(json.load(f)["cwd"]), "its folder is removed then too")

    def test_no_claude(self):
        limits, why = self.run_it()
        self.assertEqual(limits, [])
        self.assertTrue(why.startswith("找不到 claude"), why)


class TheSwitch(unittest.TestCase):
    """usage.subscription_limits: on when true or not set (owner 2026-10-16), off when false; anything else is said
    and taken as off (each reading is a request made with the owner's subscription)."""

    def round(self, value, given=True, core=None, plan=None):
        """One main() with everything around it stood in for: (read_limits called, the boards posted, stderr)."""
        settings = {"usage": {"channel": "100000000000000001"}, "plugins": {"usage": {"sources": ["host-a"]}}}
        if core is not None:
            settings["core"] = core
        if plan is not None:
            settings["usage"]["plan"] = plan
        if given:
            settings["usage"]["subscription_limits"] = value
        self.charted = []
        tmp = tempfile.mkdtemp(prefix="usage switch ")
        self.addCleanup(shutil.rmtree, tmp, True)
        called, posted = [], {}

        def setting(path, default=None):
            node = settings
            for part in path.split("."):
                if not isinstance(node, dict) or part not in node:
                    return default
                node = node[part]
            return node
        from altention import config
        inv = {"machines": {"host-a": {"ssh": ["local"]}}}
        with mock.patch.object(config, "setting", setting), \
                mock.patch.object(collect, "inventory", lambda: inv), \
                mock.patch.object(collect, "machine_usage", lambda n, m: {"machine": n, "daily": [], "projects": []}), \
                mock.patch.object(config, "settings", lambda: settings), \
                mock.patch.object(collect, "read_limits", lambda zone=None: called.append(zone) or (
                    [["Current session", 5, "7pm"], ["Current week (all models)", 9, "Oct 18, 9pm"]], None)), \
                mock.patch.object(collect, "post", lambda board, text, file=None: posted.__setitem__(board, text) or 0), \
                mock.patch.object(collect, "chart", lambda *a: self.charted.append(collect.PLAN)), \
                mock.patch.object(collect, "SAMPLES", os.path.join(tmp, "samples.jsonl")), \
                mock.patch.object(collect.time, "sleep", lambda s: None), \
                mock.patch("sys.stderr", new_callable=io.StringIO) as err:
            collect.main()
        if not os.path.exists(os.path.join(tmp, "samples.jsonl")):  # a skipped round writes no sample
            return called, posted, err.getvalue(), None
        with open(os.path.join(tmp, "samples.jsonl")) as f:
            last = json.loads(f.read().strip().split("\n")[-1])
        return called, posted, err.getvalue(), last

    def test_on_when_not_given(self):
        called, posted, err, last = self.round(None, given=False)
        self.assertTrue(called, "on by default")
        self.assertIn("這 5 小時：**5%**", posted["訂閱額度"])
        self.assertEqual(last["week"], 9)

    def test_off_when_false(self):
        called, posted, err, last = self.round(False)
        self.assertFalse(called, "no request")
        self.assertIn("不讀訂閱額度：關掉了", posted["訂閱額度"])
        self.assertIn("usage.subscription_limits 是 false", posted["訂閱額度"])
        self.assertIn("打開訂閱額度看板", posted["訂閱額度"], "says how to turn it on")
        self.assertIn("haiku", posted["訂閱額度"])
        self.assertEqual(err, "", "a plain off is not an error")
        self.assertEqual((last["limits"], last["week"]), ([], None))
        self.assertIn("這個帳號", posted, "the other boards go on")

    def test_on_when_true(self):
        called, posted, err, last = self.round(True)
        self.assertTrue(called)
        self.assertIn("這 5 小時：**5%**", posted["訂閱額度"])
        self.assertEqual(last["week"], 9)

    def test_anything_else_is_off_and_said(self):
        for value in (False, "true", 1, "yes", None, {"on": True}):
            called, posted, err, _ = self.round(value)
            self.assertFalse(called, value)
            if value is False:
                self.assertIn("關掉了", posted["訂閱額度"])
                self.assertEqual(err, "")
            else:
                self.assertIn("要是 true 或 false，現在是 %s" % json.dumps(value, ensure_ascii=False), posted["訂閱額度"])
                self.assertIn("subscription_limits", err, "and in the cron log")


class ThePlan(unittest.TestCase):
    """usage.plan: never guessed (a new user on Pro was shown "Max 20x"). Not set: the boards say so and show the
    percentages without Pro multiples; malformed: the round is skipped as before."""

    round = TheSwitch.round

    def chart_data(self, plan):
        """What collect.chart() hands chart.py for this PLAN (chart.py stood in for by a copy)."""
        tmp = tempfile.mkdtemp(prefix="usage chart ")
        self.addCleanup(shutil.rmtree, tmp, True)
        venv = os.path.join(tmp, "python")
        with open(venv, "w") as f:
            f.write('#!/bin/sh\ncp "$2" "$3"\n')
        os.chmod(venv, 0o755)
        with mock.patch.object(collect, "VENV", venv), mock.patch.object(collect, "CHART", os.path.join(tmp, "out")), \
                mock.patch.object(collect, "PLAN", plan):
            out = collect.chart("2026-10-14", [], [])
        with open(out) as f:
            return json.load(f)

    def test_not_set(self):
        called, posted, err, last = self.round(True)
        board = posted["訂閱額度"]
        self.assertIn('方案沒設定（在實例的 plugins.json 設 usage.plan，例如 ["Pro", 1]）', board)
        self.assertNotIn("目前方案", board)
        self.assertNotIn("Max 20x", board, "no guessed plan")
        self.assertIn("這週（所有模型）：**9%**", board, "the percentages are shown")
        self.assertNotIn("換算", board)
        self.assertNotIn("倍", board)
        self.assertIn("每週最高的週用量（方案沒設定）和", posted["每週"])
        self.assertNotIn("usage.plan", posted["每週"], "how to set it is said once, on the limits board")
        self.assertIn("那週：**9%**，", posted["每週"])
        self.assertNotIn("倍", posted["每週"])
        self.assertEqual(err, "")
        self.assertEqual(self.charted, [None])
        data = self.chart_data(None)
        self.assertEqual((data["plan_pro"], data["plan_name"]), (None, None))

    def test_pro(self):
        called, posted, err, last = self.round(True, plan=["Pro", 1])
        board = posted["訂閱額度"]
        self.assertIn("目前方案 Pro", board)
        self.assertNotIn("方案沒設定", board)
        self.assertIn("約 Pro 一週額度的 **0.1 倍**", board)
        self.assertIn("那週：**9%**（≈ Pro 的 0.1 倍）", posted["每週"])
        self.assertEqual(self.charted, [("Pro", 1)])
        data = self.chart_data(("Pro", 1))
        self.assertEqual((data["plan_pro"], data["plan_name"]), (1, "Pro"))

    def test_malformed_skips_the_round(self):
        for plan in (["Pro"], "Pro", ["Pro", "1"], [1, "Pro"], ["Pro", True], {"name": "Pro"}):
            called, posted, err, last = self.round(True, plan=plan)
            self.assertEqual((posted, last), ({}, None), plan)
            self.assertFalse(called, plan)
            self.assertIn("usage.plan 要是 [方案名稱, 相當於幾個 Pro]", err, plan)
            self.assertIn("這一輪跳過", err, plan)


class ThisMachinesTimeZone(unittest.TestCase):
    """Days and reset times in the time zone of the machine they are read on, none written in (manager 2026-10-14)."""

    def test_none_written_in(self):
        for name in ("collect.py", "chart.py", "remote.py"):
            with open(os.path.join(ENGINE, "usage", name), encoding="utf-8") as f:
                text = f.read()
            for fixed in ("Taipei", "hours=8", "--timezone"):
                self.assertNotIn(fixed, text, name)

    def test_ccusage_is_not_given_one(self):
        spec = importlib.util.spec_from_file_location("usage_remote", os.path.join(ENGINE, "usage", "remote.py"))
        remote = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(remote)
        seen = []
        with mock.patch.object(remote.glob, "glob", lambda p: ["/opt/node-x/bin"]), \
                mock.patch.object(remote.subprocess, "run", lambda argv, **kw: seen.append(argv) or mock.Mock(stdout="{}")):
            remote.ccusage("daily", "--since", "20261001")
        self.assertEqual(seen, [["ccusage", "daily", "--since", "20261001", "--json"]], "none: the machine's own")
        with mock.patch.object(remote.glob, "glob", lambda p: ["/opt/node-x/bin"]), \
                mock.patch.object(remote.subprocess, "run", lambda argv, **kw: seen.append(argv) or mock.Mock(stdout="{}")):
            remote.ccusage("daily", zone="Asia/Tokyo")
        self.assertEqual(seen[-1], ["ccusage", "daily", "--json", "-z", "Asia/Tokyo"], "the setting's")


class TheInstancesTimeZone(unittest.TestCase):
    """core.timezone when the instance has it, as the schedules go by (altention/scheduler.py timezone()); this
    machine's without it (#architecture-review on be69068: a server on UTC, its owner elsewhere)."""

    def zone(self, settings):
        sys.path.insert(0, os.path.join(ENGINE, "usage"))
        import usage_zone
        from altention import config
        with mock.patch.object(config, "settings", lambda: settings):
            return usage_zone.zone()

    def test_the_setting(self):
        z, name, problem = self.zone({"core": {"timezone": "Asia/Tokyo"}})
        self.assertEqual((name, problem, z.offset_at(1791975600)), ("Asia/Tokyo", None, 9 * 3600))
        # the measured sample read in it: 1791975600 is 20:00 there, 1792357200 is Oct 19 06:00
        limits, why = collect.limits_from_stream(sample(), z.local(1791970000), z)
        self.assertEqual([x[2] for x in limits], ["8pm", "Oct 19, 6am"])

    def test_none_given(self):  # this machine's (TZ is UTC in these tests); ccusage given no -z
        for settings in ({}, {"core": {}}):
            z, name, problem = self.zone(settings)
            self.assertEqual((name, problem, z.offset_at(1791975600)), (None, None, 0))

    def test_one_that_cannot_be_used(self):  # this machine's, and why (collect.py puts it in the cron log)
        for value in ("Mars/Olympus", 8, None):
            z, name, problem = self.zone({"core": {"timezone": value}})
            self.assertEqual((name, z.offset_at(1791975600)), (None, 0), value)
            self.assertIn("core.timezone", problem)

    def test_a_round_goes_by_it(self):
        sw = TheSwitch()
        sw.addCleanup = self.addCleanup
        settings = {"core": {"timezone": "Asia/Tokyo"}}
        called, posted, err, _ = sw.round(True, core=settings["core"])
        self.assertEqual(called[0].offset_at(1791975600), 9 * 3600, "read_limits is given the zone")
        self.assertEqual(err, "")
        called, posted, err, _ = sw.round(True, core={"timezone": "Mars/Olympus"})
        self.assertIn("時區設定不能用", err)


class AFreshInstance(unittest.TestCase):
    """Owner's re-install 2026-10-15: the install never sets plugins.usage.sources, so every hourly round stopped
    ("沒有用量來源") and the board never appeared, while doctor said all was well. Not set: this machine; each round
    notes how it went in ~/.cache/usage/state.json for doctor."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="usage 新 ")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.inst = os.path.join(self.tmp, "instance")
        os.makedirs(self.inst)  # no machines.json: one machine
        self.settings({"usage": {"channel": "42"}})
        env = mock.patch.dict(os.environ, {"ALT_INSTANCE": self.inst})
        env.start()
        self.addCleanup(env.stop)
        for name, value in (("SAMPLES", os.path.join(self.tmp, "share", "samples.jsonl")),
                            ("VENV", os.path.join(self.tmp, "no venv", "python")),  # no chart: matplotlib is not here
                            ("read_limits", lambda zone=None: ([], "stood in"))):  # on when not set: never a real request
            p = mock.patch.object(collect, name, value)
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(lambda: os.path.exists(collect.state_path()) and os.remove(collect.state_path()))
        self.ran, self.posted, self.post_rc = [], [], 0

    def settings(self, d):
        with open(os.path.join(self.inst, "plugins.json"), "w") as f:
            json.dump(d, f)

    def run_(self, argv, *a, **k):
        """ccusage (remote.py here) and altention dc post stood in for; nothing else is expected."""
        if argv[1:2] == [os.path.join(collect.HERE, "remote.py")]:
            self.ran.append(argv)
            today = datetime.date.today().isoformat()
            return subprocess.CompletedProcess(argv, 0, json.dumps({"daily": [{"date": today, "agent": "claude", "cost": 3.5,
                                                                               "tokens": 120000}], "projects": []}), "")
        if argv[0].endswith(os.path.join("bin", "altention")) and argv[1:3] == ["dc", "post"]:
            self.posted.append((argv[3], argv[5], argv[-1]))  # channel, board, text
            return subprocess.CompletedProcess(argv, self.post_rc, "", "")
        raise AssertionError("unexpected command: %r" % (argv,))

    def round(self):
        err = io.StringIO()
        with mock.patch.object(collect.subprocess, "run", self.run_), mock.patch.object(collect.time, "sleep", lambda s: None), \
                mock.patch("sys.stderr", err):
            try:
                collect.main_noted()
                code = 0
            except SystemExit as e:
                code = e.code
        try:
            with open(collect.state_path()) as f:
                st = json.load(f)
        except OSError:
            st = {}
        return code, st, err.getvalue()

    def samples(self):
        try:
            with open(collect.SAMPLES) as f:
                return [json.loads(l) for l in f if l.strip()]
        except OSError:
            return []

    def test_no_limits_read_before_setup_is_done(self):
        """C-48: nothing spends the subscription until #setup is done; the board says why."""
        asked = []
        self.settings({"usage": {"channel": "42", "subscription_limits": True}, "setup": {"done": False}})
        with mock.patch.object(collect, "read_limits", lambda zone=None: asked.append(1) or ([], "x")):
            self.round()
        self.assertEqual(asked, [])
        limits = [t for _, b, t in self.posted if b == "訂閱額度"]
        self.assertTrue(limits and "設定還沒做完" in limits[0], self.posted)
        self.settings({"usage": {"channel": "42", "subscription_limits": True}, "setup": {"done": True}})
        with mock.patch.object(collect, "read_limits", lambda zone=None: asked.append(1) or ([], "x")):
            self.round()
        self.assertEqual(asked, [1], "done: read as set")

    def test_sources_not_set_collects_this_machine_and_posts_the_boards(self):
        from altention import config
        code, st, err = self.round()
        self.assertEqual(code, 0, err)
        self.assertEqual(self.ran, [[sys.executable, os.path.join(ENGINE, "usage", "remote.py"), "31"]],
                         "this machine read once, by this engine's own remote.py: no pull, no cd to another engine")
        self.assertEqual(len(self.samples()), 1)
        self.assertEqual(self.samples()[0]["cost7"], 3.5)
        self.assertEqual({c for c, _, _ in self.posted}, {"42"})
        self.assertEqual([b for _, b, _ in self.posted], ["訂閱額度", "這個帳號", "每天", "專案", "每週"])
        self.assertIn("%s · Claude：今天 $3.5" % config.this_machine(), dict((b, t) for _, b, t in self.posted)["這個帳號"])
        self.assertTrue(st.get("last_run"))
        self.assertNotIn("last_failed", st)
        self.assertNotIn("last_errors", st)

    def test_set_is_taken_as_set(self):
        self.settings({"usage": {"channel": "42"}, "plugins": {"usage": {"sources": ["elsewhere"]}}})
        code, st, err = self.round()
        self.assertEqual((code, self.ran), (0, []), "a machine not on the list is not read")
        self.assertEqual(st["last_errors"], ["elsewhere：不在機器清單裡"])
        self.settings({"usage": {"channel": "42"}, "plugins": {"usage": {"sources": []}}})
        code, st, err = self.round()
        self.assertIn("plugins.usage.sources 是空的", code)
        self.assertIn("plugins.usage.sources 是空的", st["last_failed"]["why"])
        self.assertTrue(st["last_failed"]["at"])

    def test_a_round_that_fails_is_noted_and_the_next_good_one_clears_it(self):
        self.post_rc = 1
        code, st, err = self.round()
        self.assertIn("看板貼不出去", code)
        self.assertIn("看板貼不出去（訂閱額度、這個帳號、每天、專案、每週）", st["last_failed"]["why"])
        self.assertNotIn("last_run", st)
        self.post_rc = 0
        code, st, err = self.round()
        self.assertEqual(code, 0)
        self.assertNotIn("last_failed", st)
        self.assertTrue(st["last_run"])
        with mock.patch.object(collect, "inventory", side_effect=ValueError("bad list")):
            code, st, err = self.round()
        self.assertIn("機器清單讀不出來", st["last_failed"]["why"])
        self.assertTrue(st["last_run"], "the last good round stays")


class WithoutTheEngine(unittest.TestCase):
    """remote.py answers on a machine whose engine's shared code does not load (this machine's zone, said), and the
    board says what each machine ran into, not one word for every failure (#code-review on 8c64e13)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="usage 沒有引擎 ")
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def lay(self, broken, name="engine"):
        root = os.path.join(self.tmp, name)
        os.makedirs(os.path.join(root, "usage"))
        for name in ("remote.py", "usage_zone.py"):
            shutil.copy(os.path.join(ENGINE, "usage", name), os.path.join(root, "usage"))
        if broken is not None:
            os.makedirs(os.path.join(root, "altention"))
            open(os.path.join(root, "altention", "__init__.py"), "w").close()
            for name in ("config.py", "scheduler.py"):
                with open(os.path.join(root, "altention", name), "w") as f:
                    f.write(broken)
        return root

    def test_remote_answers(self):
        node = os.path.join(self.tmp, "home", ".local", "opt", "node-x", "bin")
        os.makedirs(node)
        with open(os.path.join(node, "ccusage"), "w") as f:  # a stand-in: no days, no sessions
            f.write("#!/bin/sh\necho '{}'\n")
        os.chmod(os.path.join(node, "ccusage"), stat.S_IRWXU)
        for n, broken in enumerate((None, "def x(:\n", "raise OSError(13, 'no')\n")):
            root = self.lay(broken, "engine%d" % n)  # 0: no altention beside it at all
            env = {"PATH": "/usr/bin:/bin", "HOME": os.path.join(self.tmp, "home"), "TZ": "UTC"}
            r = subprocess.run(["/usr/bin/python3", os.path.join(root, "usage", "remote.py"), "2"], cwd="/", env=env,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, timeout=60)
            self.assertEqual((r.returncode, r.stderr), (0, ""), broken)
            self.assertEqual((json.loads(r.stdout)["daily"], json.loads(r.stdout)["projects"]), ([], []), broken)

    def test_zone_without_the_engine(self):
        root = self.lay("raise OSError(13, 'no')\n")
        code = ("import sys; sys.path.insert(0, sys.argv[1]); import usage_zone; z, name, why = usage_zone.zone(); "
                "print(z.offset_at(0), name, why)")
        r = subprocess.run(["/usr/bin/python3", "-c", code, os.path.join(root, "usage")], cwd="/",
                           env={"PATH": "/usr/bin:/bin", "TZ": "UTC"}, stdout=subprocess.PIPE, universal_newlines=True,
                           timeout=30)
        self.assertTrue(r.stdout.startswith("0 None 讀不到引擎的時區設定"), r.stdout)

    def machine(self, *answers, ssh=("host-a", "host-a-lan")):
        runs = iter(answers)

        def run(argv, **kw):
            a = next(runs)
            if isinstance(a, Exception):
                raise a
            return mock.Mock(returncode=a[0], stdout=a[1], stderr=a[2])
        with mock.patch.object(collect.subprocess, "run", run), mock.patch.object(collect.os, "uname",
                                                                                  lambda: mock.Mock(nodename="elsewhere")):
            return collect.machine_usage("host-a", {"ssh": list(ssh)})

    def test_what_each_try_ran_into(self):
        got = self.machine((255, "", "ssh: connect to host host-a port 22: No route to host\n"),
                           (1, "", "Traceback (most recent call last):\n  ...\nModuleNotFoundError: No module named 'x'\n"))
        self.assertEqual(got["error"], "連不上（經 host-a）：ssh: connect to host host-a port 22: No route to host；"
                                       "remote.py 失敗（經 host-a-lan）（結束碼 1：ModuleNotFoundError: No module named 'x'）")
        got = self.machine(collect.subprocess.TimeoutExpired("ssh", 900), (0, "not json", ""))
        self.assertEqual(got["error"], "超過 900 秒沒結束（經 host-a）；remote.py 的輸出讀不懂（經 host-a-lan）")
        got = self.machine((1, "", "沒有 ccusage：先跑 usage/install-node.sh\n"), ssh=("local",))
        self.assertEqual(got["error"], "remote.py 失敗（結束碼 1：沒有 ccusage：先跑 usage/install-node.sh）")
        self.assertEqual(self.machine(ssh=())["error"], "機器清單裡沒有連得過去的別名")
        self.assertEqual(self.machine((0, '{"daily": [], "projects": []}', ""))["machine"], "host-a")

    def test_this_machine_runs_this_engines_remote_py_and_pulls_nothing(self):
        """Every install collects this machine (a release-mode engine pulled a tag unchecked every hour through
        home-pull, with no restart flag for the bot: #architecture-review on dfa579c)."""
        seen = []

        def run(argv, **kw):
            seen.append(argv)
            return mock.Mock(returncode=0, stdout='{"daily": [], "projects": []}', stderr="")
        with mock.patch.object(collect.subprocess, "run", run):
            got = collect.machine_usage("host-a", {"ssh": ["local"], "engine": "/somewhere/else"}, days=8)
        self.assertEqual(got["machine"], "host-a")
        self.assertEqual(seen, [[sys.executable, os.path.join(ENGINE, "usage", "remote.py"), "8"]])

    def test_another_machine_runs_its_remote_py_and_pulls_nothing(self):
        """Over SSH too: the command runs that engine's remote.py and nothing else; no home-pull, no git (it took a
        release there every hour, past update.py's load check, and a plain git pull on an engine without home-pull:
        C-10). The command is run by a real shell, in an engine laid out with a home-pull and a git that leave a mark."""
        root = os.path.join(self.tmp, "其他台 engine")
        marks = os.path.join(self.tmp, "marks")
        stubs = os.path.join(self.tmp, "stubs")
        for d in ("bin", "usage"):
            os.makedirs(os.path.join(root, d))
        os.makedirs(stubs)
        for path, body in ((os.path.join(root, "bin", "home-pull"), "echo home-pull >> \"%s\"\n" % marks),
                           (os.path.join(stubs, "git"), "echo git \"$@\" >> \"%s\"\n" % marks),
                           (os.path.join(root, "usage", "remote.py"),
                            "import json, sys; print(json.dumps({'daily': [], 'projects': [], 'days': sys.argv[1]}))\n")):
            with open(path, "w") as f:
                f.write(("#!/bin/sh\n" + body) if not path.endswith(".py") else body)
            os.chmod(path, stat.S_IRWXU)
        seen, real = [], subprocess.run

        def run(argv, **kw):  # ssh <options> <alias> <command>: the command, as that machine's shell would run it
            seen.append(argv)
            return real(["/bin/sh", "-c", argv[-1]], env={"PATH": stubs + ":/usr/bin:/bin"},
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, timeout=60)
        with mock.patch.object(collect.subprocess, "run", run), \
                mock.patch.object(collect.os, "uname", lambda: mock.Mock(nodename="elsewhere")):
            got = collect.machine_usage("host-a", {"ssh": ["host-a"], "engine": root}, days=8)
        self.assertEqual((got.get("error"), got["machine"], got.get("days")), (None, "host-a", "8"))
        self.assertEqual([a[:-1] for a in seen], [["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", "host-a"]])
        self.assertNotIn("pull", seen[0][-1])
        self.assertFalse(os.path.exists(marks), open(marks).read() if os.path.exists(marks) else "")


if __name__ == "__main__":
    unittest.main()
