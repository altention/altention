"""The scheduler's record-only cron entry (3a.11): it writes down what would run and never runs, posts or wakes
anything; it looks at the plugins on this machine and the schedule files they name; problems are said when they
change; its logs stay bounded with a round every minute (manager 2026-10-07)."""
import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))))
from altention import testing as _testing  # noqa: E402
_testing.guard_home()  # tests never touch the real home (altention/testing.py)
import contextlib
import datetime
import io
import json
import os
import shutil
import sys
import subprocess
import tempfile
import unittest
from unittest import mock

from altention import config, cron_schedule, engine_id, plugin, scheduler

cron_schedule.load()

T0 = 1791360000  # a whole hour


def write(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(data if isinstance(data, str) else json.dumps(data))
    return path


class Rig(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="只記錄 ")
        self.touched = os.path.join(self.tmp, "touched")
        self.files = os.path.join(self.tmp, "my schedule.json")
        write(self.files, [{"name": "remind", "when": {"every": "5m"}, "do": {"message": "m"}}])
        self.pj = write(os.path.join(self.tmp, "plug", "plugin.json"), {
            "name": "plug", "schedules": [{"name": "touch", "when": {"every": "10m"},
                                           "do": {"run": "touch %s" % self.touched, "log": os.path.join(self.tmp, "plug.out")}}],
            "schedule_files": "{setting:plug.files}"})
        rows = [("core", self.tmp, {"name": "core"}, "on", ""), ("plug", os.path.dirname(self.pj), json.loads(open(self.pj).read()), "on", ""),
                ("away", self.tmp, {"name": "away", "schedules": [{"name": "x"}]}, "off", ""),
                ("odd", self.tmp, None, "unknown", "實例設定讀不出來")]
        self.patches = [mock.patch.object(plugin, "on_this_machine", return_value=(rows, [])),
                        mock.patch.object(plugin, "this_machine", return_value="host-a"),
                        mock.patch.object(config, "settings", return_value={"plug": {"files": [self.files]}}),
                        mock.patch.object(engine_id, "check", return_value=("official", "", "")),
                        mock.patch.object(scheduler, "timezone", return_value=(scheduler.FixedZone(8 * 3600, "Asia/Taipei"), "setting", None))]
        self.no_sampling = mock.patch.object(cron_schedule, "sample_round", return_value=[])  # History turns it on
        self.patches.append(self.no_sampling)
        self.no_report = mock.patch.object(cron_schedule, "report_round", return_value=[])  # Report turns it on
        self.patches.append(self.no_report)
        for p in self.patches:
            p.start()
        shutil.rmtree(os.path.expanduser("~/.local/state/altention"), ignore_errors=True)

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def round_at(self, t):
        with mock.patch.object(scheduler.time, "time", return_value=t), mock.patch.object(cron_schedule.time, "time", return_value=t):
            return cron_schedule.main([])

    def log(self):
        try:
            with open(os.path.expanduser("~/.local/state/altention/cron-schedule.log"), encoding="utf-8") as f:
                return f.read()
        except FileNotFoundError:
            return ""


class RecordOnly(Rig):
    def test_paths_are_the_plugins_here_and_their_schedule_files(self):
        paths, labels, problems = cron_schedule.paths_here("host-a")
        self.assertEqual(paths, [self.pj, self.files])
        self.assertEqual(labels, {self.pj: "plug", self.files: "plug"})
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("odd 在不在這台跑不知道", problems[0])

    def test_nothing_is_ever_run_and_what_would_run_is_written_down(self):
        def no(*a, **k):
            raise AssertionError("record-only ran something: %r" % (a,))
        with mock.patch.object(subprocess, "Popen", no), mock.patch.object(subprocess, "run", no), \
                mock.patch.object(os, "system", no), mock.patch.object(os, "execv", no):
            self.round_at(T0)            # first sight: start points only
            self.round_at(T0 + 600)      # both due
        self.assertFalse(os.path.exists(self.touched))
        with open(scheduler.log_path("host-a"), encoding="utf-8") as f:
            ran = [json.loads(l) for l in f]
        self.assertEqual(sorted(e["item"] for e in ran if "item" in e), ["remind", "touch"])
        self.assertIn("會跑（只記錄，沒有執行）：plug/", self.log())
        self.assertFalse(os.path.exists(scheduler.sent_path("host-a")), "the real notes are never written")

    def test_problems_are_said_once_an_hour_and_when_they_are_gone(self):
        rows = [r for r in plugin.on_this_machine()[0] if r[0] != "odd"]
        with mock.patch.object(plugin, "on_this_machine", return_value=(rows, [])):
            os.remove(self.files)
            for n in range(5):
                self.round_at(T0 + 60 * n)
            self.assertEqual(self.log().count("讀不到"), 1, self.log())
            self.round_at(T0 + 3660)
            self.assertEqual(self.log().count("讀不到"), 2, "said again an hour later")
            write(self.files, "[]")
            self.round_at(T0 + 3720)
        self.assertIn("上一輪的問題都沒了", self.log())

    def test_a_place_not_known_is_said_when_it_starts_not_every_hour(self):
        """support-4 dry run #1: before the bot's token the bot's plugins have no place; said once, not all day."""
        p = "progress 在不在這台跑不知道（不知道機器人在哪台），這輪不看它的排程"
        self.assertEqual(cron_schedule.to_say("host-a", [p], T0), [p])
        self.assertEqual(cron_schedule.to_say("host-a", [p], T0 + 60), [])
        self.assertEqual(cron_schedule.to_say("host-a", [p], T0 + 5 * 3600), [], "not again while it lasts")
        self.assertEqual(cron_schedule.to_say("host-a", [], T0 + 5 * 3660), ["（上一輪的問題都沒了）"])
        self.assertEqual(cron_schedule.to_say("host-a", [p], T0 + 6 * 3600), [p], "said again when it comes back")

    def test_another_checkout_writes_nothing_at_all(self):
        """Manager 2026-10-07: as 3a.10's lines, not even a log line or a note of having said it."""
        out = io.StringIO()
        with mock.patch.object(engine_id, "check", return_value=("other", "/x", "這是 git worktree")), \
                mock.patch.object(scheduler, "record_only", side_effect=AssertionError("recorded")), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            for n in range(3):
                self.round_at(T0 + 60 * n)
        self.assertEqual(out.getvalue(), "")
        self.assertFalse(os.path.exists(os.path.expanduser("~/.local/state/altention")), "nothing written")

    def test_not_told_whose_engine_nothing_recorded_said_once_an_hour(self):
        """A machine list that cannot be read: cannot tell, so said (bounded), never recorded."""
        out = io.StringIO()
        with mock.patch.object(engine_id, "check", return_value=("unknown", "", "機器清單讀不出來（壞了），判斷不了")), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            for n in range(5):
                self.round_at(T0 + 60 * n)
            self.round_at(T0 + 3700)
        self.assertEqual(out.getvalue(), "")
        self.assertEqual(self.log().count("沒有記：機器清單讀不出來"), 2, self.log())
        self.assertFalse(os.path.exists(scheduler.sent_path("host-a", dry=True)), "nothing recorded")

    def test_tmp_files_of_killed_rounds_go_after_an_hour(self):
        state = scheduler.state_dir()
        old = write(os.path.join(state, "dry.host-a.json.4242.tmp"), "{")
        new = write(os.path.join(state, "files-dry.host-a.json.4343.tmp"), "{")
        os.utime(old, (T0 - 7200, T0 - 7200))
        os.utime(new, (T0 - 600, T0 - 600))
        with mock.patch.object(cron_schedule.time, "time", return_value=T0):
            cron_schedule.clean_tmp()
        self.assertFalse(os.path.exists(old))
        self.assertTrue(os.path.exists(new), "a round may still be writing it")

    def test_the_cron_line_says_nothing_when_its_launcher_is_gone(self):
        """Manager 2026-10-07: an engine rolled back to before 3a.11 (the supervisor, nobody there) leaves the line
        quiet, not a "can't open file" every minute."""
        core = json.loads(open(os.path.join(_os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))),
                                            "altention", "core.plugin.json")).read())
        run = next(c["run"] for c in core["cron"] if c["name"] == "schedule-record")
        gone = os.path.join(self.tmp, "rolled back 引擎")
        home = os.path.join(self.tmp, "home")
        cmd = plugin.expand(run.replace("{engine}", gone))
        r = subprocess.run(["sh", "-c", cmd], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True,
                           env=dict(os.environ, HOME=home))
        self.assertEqual((r.returncode, r.stdout), (0, ""))
        self.assertFalse(os.path.exists(os.path.join(home, ".local", "state", "altention", "cron-schedule.log")))
        here = cmd.replace(gone, _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))))
        self.assertIn("cron_schedule.py", here)

    def test_a_round_that_throws_is_one_bounded_line(self):
        out = io.StringIO()
        with mock.patch.object(scheduler, "record_only", side_effect=TypeError("boom")), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            for n in range(3):
                self.round_at(T0 + 60 * n)
        self.assertEqual(out.getvalue(), "")
        self.assertEqual(self.log().count("這一輪出錯，沒有記完：TypeError: boom"), 1, self.log())

    def test_schedule_files_as_a_list_or_with_spaces_and_one_bad_plugin(self):
        """#code-review 2026-10-07: a list crashed every round; one plugin's trouble must not end the round."""
        other = write(os.path.join(self.tmp, "other.json"), [{"name": "o", "when": {"every": "5m"}, "do": {"message": "m"}}])
        rows = [("plug", os.path.dirname(self.pj), dict(json.loads(open(self.pj).read()), schedule_files=[other]), "on", ""),
                ("spaced", self.tmp, {"name": "spaced", "schedule_files": " {setting:plug.files} "}, "on", ""),
                ("bad", self.tmp, {"name": "bad", "schedule_files": 3}, "on", "")]
        with mock.patch.object(plugin, "on_this_machine", return_value=(rows, [])):
            paths, labels, problems = cron_schedule.paths_here("host-a")
            self.round_at(T0)
            self.round_at(T0 + 600)
        self.assertEqual(paths, [self.pj, other, self.files])
        self.assertEqual(labels[self.files], "spaced")
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("bad 的排程檔", problems[0])
        with open(scheduler.log_path("host-a"), encoding="utf-8") as f:
            items = sorted(json.loads(l).get("item", "") for l in f)
        self.assertIn("o", items)
        self.assertIn("remind", items)

    def test_one_reading_of_schedule_files_for_apply_and_the_scheduler(self):
        _sys.path.insert(0, os.path.join(_os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))), "ops"))
        import applycore
        settings = {"p": {"files": ["~/a.json", "~/b.json"], "one": "~/c.json"}}
        cases = [(["~/x.json"], [os.path.expanduser("~/x.json")]), ("{setting:p.files}", [os.path.expanduser("~/a.json"), os.path.expanduser("~/b.json")]),
                 (" {setting:p.one} ", [os.path.expanduser("~/c.json")]), ("{setting:p.none}", []), ("~/d.json", [os.path.expanduser("~/d.json")]),
                 (None, [])]
        for value, want in cases:
            with self.subTest(value=value):
                d = {"name": "p", "schedule_files": value}
                self.assertEqual(plugin.schedule_files(d, self.tmp, settings), want)
                self.assertEqual([p for _, p in applycore.schedule_files([("p", self.tmp, d, "on", "")], settings)], want)
        for value in (3, [3], ["  "], {"a": 1}, "{setting:p}"):
            with self.subTest(bad=value):
                self.assertRaises(plugin.PluginError, plugin.schedule_files, {"schedule_files": value}, self.tmp, settings)
                self.assertEqual(applycore.schedule_files([("p", self.tmp, {"schedule_files": value}, "on", "")], settings), [])

    def test_whatever_any_module_or_child_writes_never_reaches_cron_and_is_said_once_an_hour(self):
        """Manager 2026-10-07: stdout and stderr taken over in main(), not chased print by print: a warning written
        at import, a print, a write to file descriptor 2, a child's stderr."""
        engine = os.path.join(self.tmp, "engine")
        shutil.copytree(_os.path.join(_os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))), "altention"),
                        os.path.join(engine, "altention"), ignore=shutil.ignore_patterns("tests", "__pycache__"))
        with open(os.path.join(engine, "altention", "scheduler.py"), "a") as f:
            f.write("\nimport sys as _s\n_s.stderr.write('匯入時的警告\\n')\n_real_record_only = record_only\n\n\n"
                    "def record_only(*a, **k):\n    import os as _o, subprocess as _p\n"
                    "    _s.stderr.write('格式比引擎新\\n')\n    print('某個 print')\n    _o.write(2, b'fd 2\\n')\n"
                    "    _p.run(['sh', '-c', 'echo child >&2'])\n    return _real_record_only(*a, **k)\n")
        home = os.path.join(self.tmp, "home")
        env = dict(os.environ, HOME=home, ALT_TEST_ANY_ENGINE="1")
        env.pop("ALT_INSTANCE", None)
        for _ in range(3):
            r = subprocess.run([_sys.executable, os.path.join(engine, "altention", "cron_schedule.py")], stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, universal_newlines=True, env=env, timeout=60)
            self.assertEqual((r.returncode, r.stdout, r.stderr), (0, "", ""))
        with open(os.path.join(home, ".local", "state", "altention", "cron-schedule.log"), encoding="utf-8") as f:
            log = f.read()
        for line in ("匯入時的警告", "格式比引擎新", "某個 print", "fd 2", "child"):
            self.assertEqual(log.count("輸出：%s" % line), 1, (line, log))

    def test_modules_that_do_not_load_give_one_bounded_line_not_a_traceback(self):
        engine = os.path.join(self.tmp, "engine")
        shutil.copytree(_os.path.join(_os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))), "altention"),
                        os.path.join(engine, "altention"), ignore=shutil.ignore_patterns("tests", "__pycache__"))
        with open(os.path.join(engine, "altention", "scheduler.py"), "a") as f:
            f.write("\ndef broken(:\n")
        home = os.path.join(self.tmp, "home")
        r = subprocess.run([_sys.executable, os.path.join(engine, "altention", "cron_schedule.py")], stdout=subprocess.PIPE,
                           stderr=subprocess.STDOUT, universal_newlines=True, env=dict(os.environ, HOME=home), timeout=60)
        self.assertEqual((r.returncode, r.stdout), (0, ""))
        with open(os.path.join(home, ".local", "state", "altention", "cron-schedule.log"), encoding="utf-8") as f:
            self.assertIn("載不進引擎的模組，沒有記：SyntaxError", f.read())

    def test_a_setting_that_is_not_paths_is_a_problem_not_a_crash(self):
        with mock.patch.object(config, "settings", return_value={"plug": {"files": 3}}):
            paths, _, problems = cron_schedule.paths_here("host-a")
        self.assertEqual(paths, [self.pj])
        self.assertTrue(any("plug 的排程檔" in p for p in problems), problems)

    def compare(self, now):
        out = io.StringIO()
        with mock.patch.object(cron_schedule.time, "time", return_value=now):
            cron_schedule.compare(24, out, now=now)
        return out.getvalue()

    def with_trace(self, trace):
        d = json.loads(open(self.pj).read())
        d["schedules"][0]["do"]["trace"] = trace
        write(self.pj, d)

    def test_compare_says_for_each_item_whether_it_ran(self):
        """Manager 2026-10-08: a trace every run brings up to date, and the answer said, not two times side by side."""
        state = os.path.join(self.tmp, "state.json")
        self.with_trace({"file": state, "key": "last.at"})
        self.round_at(T0)
        self.round_at(T0 + 600)
        write(os.path.join(self.tmp, "plug.out"), "ran long ago\n")
        os.utime(os.path.join(self.tmp, "plug.out"), (T0 - 86400, T0 - 86400))  # nothing written when nothing was said
        write(state, {"last": {"at": T0 + 605}})
        text = self.compare(T0 + 1000)
        self.assertIn("[相符] plug/touch：會跑 1 次", text)
        self.assertIn("state.json 的 last.at", text)
        self.assertIn("remind：會跑 1 次，沒有 trace", text)
        self.assertIn("[無法判斷]", text)
        self.assertIn("共 2 項：相符 1／時間不同 0／不相符 0／無法判斷 1；會跑的紀錄涵蓋 ", text)
        self.assertIn("；還沒有痕跡的歷史", text)
        later = datetime.datetime.fromtimestamp(T0 + 2400, datetime.timezone(datetime.timedelta(hours=8)))
        write(state, {"last": {"at": later.isoformat()}})  # an ISO time, 30 minutes after the one scheduled
        text = self.compare(T0 + 3000)
        self.assertIn("[時間不同] plug/touch", text)
        self.assertIn("時間不同 1", text)

    def test_a_trace_by_its_modification_time_and_a_log_is_no_evidence(self):
        trace = os.path.join(self.tmp, "touched by every run")
        self.round_at(T0)
        self.round_at(T0 + 600)  # this line has no trace: the newest line is what counts
        self.with_trace(trace)
        self.round_at(T0 + 1200)
        write(trace, "")
        os.utime(trace, (T0 + 1210, T0 + 1210))
        text = self.compare(T0 + 1500)
        self.assertIn("[相符] plug/touch：會跑 2 次", text)
        os.utime(trace, (T0 + 610, T0 + 610))
        self.assertIn("[不相符] plug/touch", self.compare(T0 + 1500))

    def test_a_trace_written_wrong_never_stops_the_item_and_is_said(self):
        self.with_trace({"key": "x"})
        self.round_at(T0)
        self.round_at(T0 + 600)
        with open(scheduler.log_path("host-a"), encoding="utf-8") as f:
            self.assertIn("touch", f.read(), "the item was still taken")
        text = self.compare(T0 + 1000)
        self.assertIn("[無法判斷] plug/touch", text)
        self.assertIn("trace 要是檔案路徑", text)
        self.with_trace({"file": os.path.join(self.tmp, "not there"), "within": "10m"})
        self.round_at(T0 + 1200)
        self.assertIn("痕跡檔讀不到", self.compare(T0 + 1500))


class History(Rig):
    """Manager 2026-10-08: items sharing a trace (planner.out) each checked at every one of their times, from a
    history of the trace the minute rounds sample after they are done; a stretch not sampled is never 不相符."""

    def setUp(self):
        super().setUp()
        self.no_sampling.stop()
        self.patches.remove(self.no_sampling)
        self.state = os.path.join(self.tmp, "state.json")
        d = json.loads(open(self.pj).read())
        d["schedules"][0]["do"]["trace"] = {"file": self.state, "key": "at"}
        write(self.pj, d)

    def rounds(self, start, end, writes=()):
        """A round every minute from start to end; writes: {minute: time} put in the trace before that round."""
        writes = dict(writes)
        for t in range(start, end + 1, 60):
            if t in writes:
                write(self.state, {"at": writes[t]})
            self.round_at(t)

    def touch_line(self, now):
        out = io.StringIO()
        cron_schedule.compare(24, out, now=now)
        line = [l for l in out.getvalue().splitlines() if "plug/touch" in l]
        self.assertEqual(len(line), 1, out.getvalue())
        return line[0]

    def test_every_time_is_checked_and_a_stretch_not_sampled_is_not_a_miss(self):
        self.rounds(T0, T0 + 1740, {T0 + 660: T0 + 605, T0 + 1260: T0 + 1205})
        self.assertIn("[相符] plug/touch：會跑 2 次，2 次都對得到更新", self.touch_line(T0 + 1750))
        self.round_at(T0 + 3000)  # asleep just after the round that took 3000
        self.rounds(T0 + 3420, T0 + 3540)
        line = self.touch_line(T0 + 3550)
        self.assertIn("[無法判斷]", line)
        self.assertIn("1 次那段沒有取樣", line)
        self.rounds(T0 + 3600, T0 + 4140, {T0 + 3660: T0 + 3630, T0 + 3960: T0 + 3950})
        line = self.touch_line(T0 + 4150)
        self.assertIn("[時間不同]", line, "3950 is no scheduled time")
        self.rounds(T0 + 4200, T0 + 4740)
        line = self.touch_line(T0 + 4750)
        self.assertIn("[不相符]", line)
        self.assertIn("有 1 次沒有更新", line)
        with open(cron_schedule.history_paths("host-a")[0], encoding="utf-8") as f:
            spans = [json.loads(l)["span"] for l in f if '"span"' in l]
        self.assertEqual(spans, [[T0, T0 + 1740], [T0 + 3000, T0 + 3000]], "each round's sampling time is kept")

    def test_items_sharing_a_trace_each_have_their_own_times(self):
        """morning, evening and weekly all bring planner.out up to date: a later one never hides an earlier one."""
        out_file = os.path.join(self.tmp, "planner.out")
        d = json.loads(open(self.pj).read())
        d["schedules"] = [{"name": "morning", "when": {"every": "20m"}, "do": {"message": "m", "trace": out_file}},
                          {"name": "evening", "when": {"every": "20m", "offset": "10m"}, "do": {"message": "e", "trace": out_file}}]
        write(self.pj, d)
        for t in range(T0, T0 + 3000, 60):
            if t % 600 == 60 and t > T0 + 600:
                write(out_file, "已送出\n")
                os.utime(out_file, (t - 50, t - 50))
            self.round_at(t)
        out = io.StringIO()
        cron_schedule.compare(24, out, now=T0 + 3000)
        self.assertIn("[相符] plug/morning", out.getvalue())
        self.assertIn("[相符] plug/evening", out.getvalue())
        self.assertIn("痕跡的歷史涵蓋", out.getvalue().splitlines()[-1])

    def test_where_nothing_is_kept_nothing_is_concluded(self):
        """Manager 2026-10-08: an update before the would-run log's oldest line is no 時間不同; a trace never once
        readable is 無法判斷 (痕跡檔讀不到), not 不相符."""
        how = (self.state, "at", 300)
        tid = cron_schedule.trace_id(self.state, "at")
        times = [(T0 + 3600, how), (T0 + 7200, how)]
        hist = {"updates": {tid: [T0 + 500, T0 + 1000, T0 + 3630, T0 + 7230]}, "spans": [(T0, T0 + 9000)], "first": T0,
                "starts": {tid: T0}, "initial": {tid: T0 + 500}}
        ours = {tid: [T0 + 3600, T0 + 7200]}
        verdict, _ = cron_schedule.judge_history(times, hist, ours, {}, T0 + 9000, T0, recorded=T0 + 3600)
        self.assertEqual(verdict, "相符", "T0 + 1000 belongs to a time whose line is gone")
        self.assertEqual(cron_schedule.judge_history(times, hist, ours, {}, T0 + 9000, T0, recorded=T0)[0], "時間不同",
                         "with the line kept it is one")
        d = json.loads(open(self.pj).read())
        d["schedules"][0]["do"]["trace"] = os.path.join(self.tmp, "a path written wrong")
        write(self.pj, d)
        self.rounds(T0, T0 + 1800)
        line = self.touch_line(T0 + 1810)
        self.assertIn("[無法判斷]", line)
        self.assertIn("痕跡檔讀不到", line)

    def test_sampling_comes_after_the_round_and_never_holds_it(self):
        fifo = os.path.join(self.tmp, "hangs")
        os.mkfifo(fifo)  # reading it waits for ever, like a file system that hangs
        d = json.loads(open(self.pj).read())
        d["schedules"][0]["do"]["trace"] = {"file": fifo, "key": "at"}
        write(self.pj, d)
        with mock.patch.object(cron_schedule, "SAMPLE_LIMIT", 2):
            self.round_at(T0)
        mark = json.loads(open(scheduler.round_paths("host-a", dry=True)[0]).read())
        self.assertEqual(mark["at"], T0, "the round is done and marked")
        self.assertIn("取樣超過 2 秒", self.log())
        ours = subprocess.Popen([_sys.executable, "-c", "import time; time.sleep(60)", "cron_schedule.py", "--sample"])
        self.addCleanup(lambda: (ours.kill(), ours.wait()))
        write(cron_schedule.history_paths("host-a")[2], "%d\n" % ours.pid)  # one still there
        d["schedules"][0]["do"]["trace"] = self.state
        write(self.pj, d)
        write(self.state, "{}")
        self.round_at(T0 + 3700)
        self.assertIn("上一次的取樣還沒結束", self.log())
        self.assertFalse(os.path.exists(cron_schedule.history_paths("host-a")[0]), "no second one started")
        with mock.patch.object(cron_schedule, "sample_round", side_effect=RuntimeError("boom")):
            self.round_at(T0 + 7400)
        self.assertEqual(json.loads(open(scheduler.round_paths("host-a", dry=True)[0]).read())["at"], T0 + 7400)
        self.assertIn("取樣出錯：RuntimeError: boom", self.log())

    def test_a_pid_file_that_is_not_a_sampling_of_ours_is_none(self):
        """#code-review 2026-10-08: empty, 0, below 0, gone, or another live process: the next round samples."""
        other = subprocess.Popen(["sleep", "60"])
        self.addCleanup(lambda: (other.kill(), other.wait()))
        gone = subprocess.Popen(["true"])
        gone.wait()
        pid_path = cron_schedule.history_paths("host-a")[2]
        write(self.state, {"at": T0})
        for n, text in enumerate(["", "0\n", "-1\n", "%d\n" % gone.pid, "%d\n" % other.pid, "not a number"]):
            with self.subTest(pid=text):
                write(pid_path, text)
                write(self.state, {"at": T0 + n})
                self.round_at(T0 + 60 * n)
                updates = cron_schedule.read_history("host-a")["updates"]
                self.assertIn(T0 + n, updates.get(cron_schedule.trace_id(self.state, "at"), []), self.log())
                self.assertFalse(os.path.exists(pid_path), "taken over and removed when done")

    def test_pm_may_take_nine_minutes(self):
        progress = json.loads(open(os.path.join(os.path.dirname(os.path.dirname(cron_schedule.__file__)), "progress", "plugin.json")).read())
        self.assertEqual(cron_schedule.trace_of(progress["schedules"][0]["do"]["trace"])[2], 540)

    def test_a_trace_is_only_read(self):
        write(self.state, {"at": T0})
        os.utime(self.state, (T0 - 100, T0 - 100))
        before = sorted(os.listdir(self.tmp)), open(self.state).read()
        self.rounds(T0, T0 + 300)
        self.assertEqual((sorted(os.listdir(self.tmp)), open(self.state).read()), before)
        self.assertEqual(os.path.getmtime(self.state), T0 - 100)

    def test_the_history_stays_bounded(self):
        cron_schedule.load()
        traces = [(self.state, "at")]
        with mock.patch.object(cron_schedule, "HISTORY_MAX", 4096):
            for n in range(1500):
                write(self.state, {"at": T0 + n})
                cron_schedule.sample("host-a", traces, T0 + 60 * n)
        history = cron_schedule.history_paths("host-a")[0]
        self.assertLessEqual(os.path.getsize(history), 4096 + 200)
        self.assertLessEqual(os.path.getsize(history + ".1"), 4096 + 200)
        self.assertFalse(os.path.exists(history + ".2"))
        hist = cron_schedule.read_history("host-a")
        updates, spans = hist["updates"], hist["spans"]
        self.assertEqual(max(updates[cron_schedule.trace_id(self.state, "at")]), T0 + 1499)
        self.assertEqual(spans[-1][1], T0 + 60 * 1499)
        self.assertGreater(spans[-1][0], T0, "starts no earlier than the oldest line still kept")

    def test_a_time_the_history_no_longer_holds_is_not_a_miss(self):
        """#code-review-2 2026-10-08: one stretch of sampling longer than the history kept (renamed away twice)
        made times before the oldest line 不相符; they are 無法判斷 (歷史已輪替), and the count says from when."""
        cron_schedule.load()
        tid = cron_schedule.trace_id(self.state, "at")
        slots = [T0 + 600 * n for n in range(1, 300)]
        with mock.patch.object(cron_schedule, "HISTORY_MAX", 4096):
            for n in range(0, 300 * 10):  # a round every minute, the trace brought up to date 30 s after each slot
                t = T0 + 60 * n
                write(self.state, {"at": max([x + 30 for x in slots if x + 30 <= t], default=T0)})
                cron_schedule.sample("host-a", [(self.state, "at")], t)
        hist = cron_schedule.read_history("host-a")
        first = hist["first"]
        self.assertGreater(first, slots[0], "the oldest lines were renamed away")
        now = T0 + 60 * 3000
        how = (self.state, "at", 300)
        verdict, why = cron_schedule.judge_history([(x, how) for x in slots], hist, {tid: slots}, {}, now, T0)
        self.assertEqual(verdict, "無法判斷", why)
        self.assertIn("歷史已輪替", why)
        self.assertNotIn("沒有更新", why)
        late = [(x, how) for x in slots if x >= first]
        self.assertEqual(cron_schedule.judge_history(late, hist, {tid: slots}, {}, now, first)[0], "相符")

    def every_ten_minutes(self, start, end, state=None):
        """Rounds every minute; the trace brought up to date 30 s after each of touch's times (every 10 m). The
        sampling in this process (the child is covered by the tests above): hours of rounds stay quick."""
        here = mock.patch.object(cron_schedule, "sample_round",
                                 lambda machine, paths, now: cron_schedule.sample(machine, cron_schedule.traces_here(paths), now))
        here.start()
        self.addCleanup(here.stop)
        for t in range(start, end + 1, 60):
            if t % 600 == 60:
                write(state or self.state, {"at": t - 30})
            self.round_at(t)

    def test_a_trace_changed_is_judged_by_the_one_each_line_named(self):
        """#code-review 2026-10-08: after a change of trace, the earlier times were judged by the new one and
        came out 不相符 (143 times out of 119); each time is judged by its own line's trace (manager)."""
        other = os.path.join(self.tmp, "the new trace.json")
        self.every_ten_minutes(T0, T0 + 6 * 3600 + 60)  # the run of the last time named by the old one wrote the old one
        d = json.loads(open(self.pj).read())
        d["schedules"][0]["do"]["trace"] = {"file": other, "key": "at"}
        write(self.pj, d)
        self.every_ten_minutes(T0 + 6 * 3600 + 120, T0 + 10 * 3600, other)
        line = self.touch_line(T0 + 10 * 3600 + 60)
        self.assertIn("[相符]", line)
        self.assertIn("視窗內 trace 換過：前 ", line)
        self.assertIn("the new trace.json", line)

    def test_a_trace_changed_and_changed_back_is_no_other_time(self):
        """#code-review 2026-10-08 (low): A, then B, then A again, the runs writing A all along: A's updates
        while the lines named B were at no time that named A, and came out 時間不同."""
        other = os.path.join(self.tmp, "the new trace.json")
        first = json.loads(open(self.pj).read())
        self.every_ten_minutes(T0, T0 + 3 * 3600 + 60)
        d = json.loads(json.dumps(first))
        d["schedules"][0]["do"]["trace"] = {"file": other, "key": "at"}
        write(self.pj, d)
        for t in range(T0 + 3 * 3600 + 120, T0 + 6 * 3600 + 61, 60):
            if t % 600 == 60:
                write(self.state, {"at": t - 30})
            self.every_ten_minutes(t, t, other)
        write(self.pj, first)
        self.every_ten_minutes(T0 + 6 * 3600 + 120, T0 + 9 * 3600)
        line = self.touch_line(T0 + 9 * 3600 + 60)
        self.assertIn("[相符]", line)
        self.assertIn("接著 ", line)

    def test_a_trace_written_later_leaves_the_times_before_it_unjudged(self):
        d = json.loads(open(self.pj).read())
        plain = json.loads(json.dumps(d))
        del plain["schedules"][0]["do"]["trace"]
        plain["schedules"].append({"name": "keeps sampling", "when": {"every": "10m"},
                                   "do": {"run": "x", "trace": os.path.join(self.tmp, "another")}})
        write(os.path.join(self.tmp, "another"), "")
        write(self.pj, plain)
        self.every_ten_minutes(T0, T0 + 4 * 3600)
        d["schedules"].append(plain["schedules"][1])
        write(self.pj, d)
        self.every_ten_minutes(T0 + 4 * 3600 + 60, T0 + 8 * 3600)
        line = self.touch_line(T0 + 8 * 3600 + 60)
        self.assertIn("[無法判斷]", line)
        self.assertIn("那時沒有 trace", line)
        self.assertNotIn("沒有更新", line)
        self.assertIn("前 ", line)
        self.every_ten_minutes(T0 + 8 * 3600 + 60, T0 + 13 * 3600)
        out = io.StringIO()
        cron_schedule.compare(4, out, now=T0 + 13 * 3600 + 60)
        self.assertIn("[相符] plug/touch", out.getvalue(), "a window after the change judges as usual")


class Report(Rig):
    """3a.12, report only: every quarter of an hour, at the machine's own minute, what apply would change is worked
    out by a child and kept beside the scheduler's state. Nothing here applies anything."""

    def setUp(self):
        Rig.setUp(self)
        self.no_report.stop()
        self.patches.remove(self.no_report)
        self.said = {"format": 1, "machine": "host-a", "engine": "aaa", "instance": "bbb", "add": [], "remove": [],
                     "retime": [], "core": [], "refuse": None}
        self.child = os.path.join(self.tmp, "the child.py")
        self.answer = os.path.join(self.tmp, "answer.json")
        write(self.child, "")
        with open(self.child, "w") as f:
            f.write("import sys, time\nopen(%r, 'a').write('ran\\n')\ntext = open(%r).read()\n"
                    "if text.startswith('SLEEP'):\n    time.sleep(30)\n"
                    "sys.stdout.write(text)\nsys.exit(3 if text.startswith('FAIL') else 0)\n" % (self.child + ".ran", self.answer))
        self.answers(self.said)
        p = mock.patch.object(cron_schedule, "report_command", return_value=[sys.executable, self.child])
        p.start()
        self.patches.append(p)
        self.minute = scheduler.machine_offset(900, "host-a") // 60 % 15
        self.due = T0 - T0 % 900 + self.minute * 60 + 900  # a round of this machine's quarter hour

    def answers(self, what):
        with open(self.answer, "w", encoding="utf-8") as f:
            f.write(what if isinstance(what, str) else json.dumps(what, ensure_ascii=False))

    def ran(self):
        try:
            with open(self.child + ".ran") as f:
                return len(f.read().splitlines())
        except FileNotFoundError:
            return 0

    def kept(self):
        return cron_schedule.report_paths("host-a")

    def report(self):
        with open(self.kept()[0], encoding="utf-8") as f:
            return json.load(f)

    def history(self):
        try:
            with open(self.kept()[1], encoding="utf-8") as f:
                return [json.loads(l) for l in f]
        except FileNotFoundError:
            return []

    def test_once_a_quarter_of_an_hour_at_this_machines_minute(self):
        for t in range(self.due - 900 + 60, self.due + 900, 60):
            self.round_at(t)
        self.assertEqual(self.ran(), 1, "one round in fifteen works it out")
        self.assertEqual(self.report()["at"], self.due)
        self.assertEqual(self.report()["engine"], "aaa")
        self.assertFalse(os.path.exists(self.kept()[2]), "the pid file is gone when it is done")

    def test_the_history_gets_a_line_only_when_what_it_says_changes(self):
        self.round_at(self.due)
        self.round_at(self.due + 900)
        self.assertEqual(len(self.history()), 1)
        self.assertEqual(self.report()["at"], self.due + 900, "the latest report is always the latest")
        self.answers(dict(self.said, engine="ccc", instance="ddd"))
        self.round_at(self.due + 1800)
        self.assertEqual(len(self.history()), 1, "another commit, the same findings: no new line")
        self.answers(dict(self.said, add=[{"plugin": "progress", "name": "run", "kind": "cron", "status": "missing"}]))
        self.round_at(self.due + 2700)
        self.assertEqual([len(h["add"]) for h in self.history()], [0, 1])

    def test_a_history_past_its_size_is_renamed(self):
        with mock.patch.object(cron_schedule, "REPORT_HISTORY_MAX", 10):
            self.round_at(self.due)
            self.answers(dict(self.said, refuse="mass-removal：x"))
            self.round_at(self.due + 900)
        self.assertTrue(os.path.exists(self.kept()[1] + ".1"))
        self.assertEqual(len(self.history()), 1)

    def test_a_child_that_fails_or_says_something_else_is_a_report_of_that(self):
        for what in ("FAIL and a traceback", "not json at all", json.dumps({"format": 2}), json.dumps([1])):
            self.answers(what)
            self.round_at(self.due)
            self.assertIn("算不出來", self.report()["failed"], what)
            self.assertNotIn("add", self.report(), "never read as nothing to change")

    def test_a_child_that_hangs_is_left_behind_and_said(self):
        self.answers("SLEEP")
        with mock.patch.object(cron_schedule, "REPORT_LIMIT", 0.5):
            self.round_at(self.due)
        self.assertIn("套用報告超過", self.log())
        self.assertFalse(os.path.exists(self.kept()[0]), "no report written from it")

    def test_another_one_still_running_is_not_joined_by_a_second(self):
        other = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)", "plugins.py", "report"])
        try:
            write(self.kept()[2], "")
            with open(self.kept()[2], "w") as f:
                f.write("%d\n" % other.pid)
            self.round_at(self.due)
            self.assertEqual(self.ran(), 0)
            self.assertIn("還沒算完", self.log())
        finally:
            other.kill()
            other.wait()

    def test_off_is_not_even_worked_out_and_any_other_word_only_reports(self):
        with mock.patch.object(config, "setting", return_value="off"):
            self.round_at(self.due)
        self.assertEqual(self.ran(), 0)
        with mock.patch.object(config, "setting", return_value="write"):
            self.round_at(self.due)
        self.assertEqual(self.ran(), 1)
        self.assertIn("只會報告，不會寫", self.report()["note"])

    def test_another_checkout_writes_nothing(self):
        with mock.patch.object(engine_id, "check", return_value=("other", "", "")):
            self.round_at(self.due)
        self.assertEqual(self.ran(), 0)
        self.assertFalse(os.path.exists(self.kept()[0]))

    def test_a_report_that_goes_wrong_never_undoes_the_round(self):
        with mock.patch.object(cron_schedule, "report_command", side_effect=RuntimeError("boom")):
            self.round_at(self.due)
        self.assertIn("套用報告出錯", self.log())
        self.assertIsNotNone(scheduler.last_good_round("host-a", dry=True))

    def test_the_round_starts_the_reading_command_and_nothing_that_writes(self):
        """What the round runs is `ops/plugins.py report` (ops/tests check that it writes nothing); the round's
        own module never names the half that writes."""
        for p in self.patches:
            if getattr(p, "attribute", "") == "report_command":
                p.stop()
                self.patches.remove(p)
        self.assertEqual(cron_schedule.report_command()[1:], [os.path.join(cron_schedule.ENGINE, "ops", "plugins.py"), "report"])
        with open(cron_schedule.__file__.replace(".pyc", ".py"), encoding="utf-8") as f:
            source = f.read()
        self.assertNotIn("applycore", source)
        self.assertNotIn("--write\"", source)
        self.assertNotIn("\"restore\"", source)


class HomeGuard(unittest.TestCase):
    def test_the_real_rounds_sampling_is_not_taken_for_the_tests(self):
        """2026-10-08: a deploy stopped because the real minute round, already on the new code, wrote its trace
        history into the real home while the tests ran."""
        from altention import testing
        home = tempfile.mkdtemp(prefix="家 ")
        try:
            d = os.path.join(home, ".local", "state", "altention", "scheduler")
            for name in ("trace-history.m.jsonl", "trace-history.m.jsonl.1", "trace-seen.m.json", "trace-sample.m.pid",
                         "said-dry.sample-m.json", "other.json", "apply-report.m.json", "apply-report.m.jsonl",
                         "apply-report.m.jsonl.1", "apply-reporting.m.pid", "said-dry.report-m.json"):
                write(os.path.join(d, name), "x")
            seen = testing._snapshot(home)
            self.assertIsNone(seen[os.path.join(d, "trace-seen.m.json")], "its time is not compared")
            self.assertIsNone(seen[os.path.join(d, "said-dry.sample-m.json")])
            self.assertNotIn(os.path.join(d, "trace-history.m.jsonl.1"), seen, "rotated by the real round")
            self.assertNotIn(os.path.join(d, "trace-sample.m.pid"), seen, "there only while a round samples")
            self.assertIsNone(seen[os.path.join(d, "apply-report.m.json")], "3a.12's report: the real round's")
            self.assertIsNone(seen[os.path.join(d, "apply-report.m.jsonl")])
            self.assertNotIn(os.path.join(d, "apply-report.m.jsonl.1"), seen)
            self.assertNotIn(os.path.join(d, "apply-reporting.m.pid"), seen)
            self.assertIsNone(seen[os.path.join(d, "said-dry.report-m.json")])
            self.assertIsNotNone(seen[os.path.join(d, "other.json")], "anything else still is")
        finally:
            shutil.rmtree(home)

    def test_what_the_real_rounds_write_in_the_state_folder_is_not_taken_for_the_tests(self):
        """2026-10-17: once the pre-push hooks were on our machines, a real push wrote remote-checks.json while the
        suites ran, and every suite said the tests had touched the real home. The files that come and go (deps-pending,
        update-available, instance-held) stay compared: the tests write them a lot (#code-review of 42a1663)."""
        from altention import testing
        home = tempfile.mkdtemp(prefix="家 ")
        try:
            d = os.path.join(home, ".local", "state", "altention")
            for name in ("remote-checks.json", "update-mode", "deps-pending.lock", "deps-pending", "update-available",
                         "instance-held", "other.json"):
                write(os.path.join(d, name), "x")
            seen = testing._snapshot(home)
            for name in ("remote-checks.json", "update-mode", "deps-pending.lock"):
                self.assertIsNone(seen[os.path.join(d, name)], "%s: its time is not compared" % name)
            for name in ("deps-pending", "update-available", "instance-held", "other.json"):
                self.assertIsNotNone(seen[os.path.join(d, name)], "%s: still compared" % name)
        finally:
            shutil.rmtree(home)

class Judge(unittest.TestCase):
    H = 3600

    def judge(self, mine, t, now, ours=None, others=(), within=300):
        return cron_schedule.judge(mine, sorted(ours or mine), sorted(others), t, within, now)

    def test_the_four_answers(self):
        two = [T0, T0 + self.H]
        self.assertEqual(self.judge(two, T0 + self.H + 60, T0 + 2 * self.H)[0], "相符")
        verdict, why = self.judge(two, T0 + self.H + 1800, T0 + 2 * self.H)
        self.assertEqual(verdict, "時間不同", "the schedule says one time, what runs another")
        self.assertEqual(why.count(":"), 2, "both times printed")
        verdict, why = self.judge(two, T0 + 60, T0 + 2 * self.H)
        self.assertEqual(verdict, "不相符")
        self.assertIn("對得上", why)
        self.assertEqual(self.judge(two, T0 - 5000, T0 + 2 * self.H)[0], "不相符")

    def test_the_last_may_still_be_running(self):
        two = [T0, T0 + self.H]
        verdict, why = self.judge(two, T0 + 30, T0 + self.H + 60)
        self.assertEqual(verdict, "相符")
        self.assertIn("可能還在跑", why)
        self.assertEqual(self.judge(two, T0 + self.H + 50, T0 + self.H + 60)[0], "相符", "or already done")
        self.assertEqual(self.judge([T0], T0 - 9000, T0 + 60)[0], "無法判斷")

    def test_a_trace_shared_or_written_by_others(self):
        verdict, why = self.judge([T0], T0 + self.H + 10, T0 + 2 * self.H, ours=[T0, T0 + self.H])
        self.assertEqual(verdict, "無法判斷", "a later item of the same trace covers this one")
        self.assertIn("較晚的另一項", why)
        verdict, why = self.judge([T0], T0 + self.H + 5, T0 + 2 * self.H, others=[T0 + self.H])
        self.assertEqual(verdict, "無法判斷")
        self.assertIn("輸出寫到同一個檔", why)

    def test_what_a_trace_is(self):
        self.assertEqual(cron_schedule.trace_of("~/x"), (os.path.expanduser("~/x"), None, cron_schedule.WITHIN))
        self.assertEqual(cron_schedule.trace_of({"file": "/a", "key": "k", "within": "10m"}), ("/a", "k", 600))
        for bad in (5, "", {"file": ""}, {"file": "/a", "key": 3}, {"file": "/a", "within": "soon"},
                    {"file": "/a", "within": 0}, {"file": "/a", "within": True}):
            with self.subTest(bad=bad):
                self.assertRaises(ValueError, cron_schedule.trace_of, bad)

    def test_a_time_under_a_key(self):
        tmp = tempfile.mkdtemp(prefix="痕跡 ")
        try:
            path = write(os.path.join(tmp, "s.json"), {"a": {"t": "2026-10-08T08:40:11+08:00"}, "n": 12.5, "s": "x", "b": True})
            self.assertEqual(cron_schedule.trace_time(path, "a.t"), 1791420011)
            self.assertEqual(cron_schedule.trace_time(path, "n"), 12.5)
            for key in ("s", "b", "missing", "a.t.u"):
                with self.subTest(key=key):
                    self.assertRaises(ValueError, cron_schedule.trace_time, path, key)
            write(path, "{not json")
            self.assertRaises(ValueError, cron_schedule.trace_time, path, "a")
        finally:
            shutil.rmtree(tmp)


class Marks(Rig):
    def test_a_mark_in_the_future_is_left_out_before_the_later_is_taken(self):
        """#code-review-2 2026-10-07: the mark beside the home is shared by every home of this user."""
        state_mark, beside_mark = scheduler.round_paths("host-a", dry=True)
        write(state_mark, {"at": T0 - 900})
        write(beside_mark, {"at": T0 + 7200})
        future = []
        self.assertEqual(scheduler.last_good_round("host-a", dry=True, now=T0, future=future), T0 - 900)
        self.assertEqual(future, [T0 + 7200])
        write(state_mark, {"at": T0 + 3600})
        self.assertIsNone(scheduler.last_good_round("host-a", dry=True, now=T0))
        write(beside_mark, {"at": T0 + 60})  # within the slack: a clock a little ahead is believed
        self.assertEqual(scheduler.last_good_round("host-a", dry=True, now=T0), T0 + 60)


class Bounded(Rig):
    """Manager 2026-10-07: a round every minute for a day, its logs stay within their limits (rotation works)."""

    def test_a_day_of_rounds_with_a_problem_every_round(self):
        os.remove(self.files)  # a problem line in would-run every round, the worst everyday case
        with mock.patch.object(scheduler, "LOG_MAX", 20 * 1024), mock.patch.object(cron_schedule, "LOG_MAX", 4 * 1024), \
                mock.patch.object(cron_schedule, "AGAIN", 0):  # and said every round, too
            for n in range(1440):
                self.round_at(T0 + 60 * n)
        state = scheduler.state_dir()
        would = scheduler.log_path("host-a")
        self.assertLessEqual(os.path.getsize(would), 20 * 1024 + 2048)
        self.assertTrue(os.path.exists(would + ".1"), "rotated")
        self.assertFalse(os.path.exists(would + ".2"))
        log = os.path.expanduser("~/.local/state/altention/cron-schedule.log")
        self.assertLessEqual(os.path.getsize(log), 4 * 1024 + 2048)
        self.assertTrue(os.path.exists(log + ".1"))
        total = sum(os.path.getsize(os.path.join(state, f)) for f in os.listdir(state))
        self.assertLess(total, 2 * 20 * 1024 + 64 * 1024, sorted(os.listdir(state)))


def _real_beside():
    """The real home's folder beside it (scheduler.beside_default, worked out for the real home, not this suite's)."""
    import hashlib
    import pwd
    home = os.path.realpath(pwd.getpwuid(os.getuid()).pw_dir)
    return "/tmp/altention-%d-%s" % (os.getuid(), hashlib.sha256(os.fsencode(home)).hexdigest()[:8])


_BESIDE = _real_beside()  # the real one, never this suite's (ALT_RUNTIME: testing.guard_home)


def _snapshot():
    try:
        return {n: os.stat(os.path.join(_BESIDE, n)).st_mtime_ns for n in os.listdir(_BESIDE)}
    except OSError:
        return {}


_BEFORE = _snapshot()


def _the_real_rounds(name):
    """Was `name` written by this machine's real record-only round (its cron line, every minute since 3a.11)? That
    round writes the same mark into the real state folder too; a test missing ALT_RUNTIME would not (its HOME is
    the throwaway one)."""
    import pwd
    real = os.path.join(pwd.getpwuid(os.getuid()).pw_dir, ".local/state/altention/scheduler", name[len("scheduler-"):])
    for _ in range(3):  # the round writes the state folder's first: a moment between the two
        try:
            with open(os.path.join(_BESIDE, name), "rb") as a, open(real, "rb") as b:
                if name.startswith("scheduler-") and a.read() == b.read():
                    return True
        except OSError:
            return False
        import time
        time.sleep(1)
    return False


def tearDownModule():
    """Manager 2026-10-07: after the whole suite nothing in the real /tmp/altention-<uid>/ is new or rewritten
    (by the tests: the real round of every minute is told apart by its twin in the real state folder)."""
    touched = sorted(n for n, t in _snapshot().items() if _BEFORE.get(n) != t and not _the_real_rounds(n))
    if touched:
        raise AssertionError("the tests wrote into the real %s: %s" % (_BESIDE, touched))


if __name__ == "__main__":
    unittest.main()
