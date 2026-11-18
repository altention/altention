"""C-98: what was spent in the last 7 and 30 days, per conversation (usage/remote.py splits each one's cost over its
days, as its transcripts used them, scaled to ccusage's own day totals), and the projects board counting only those
7 days. Transcripts are made up in a scratch HOME; ccusage is stood in for with rows in its own JSON shape
(ccusage 20.0.26: session rows {period, agent, totalCost, totalTokens, metadata.lastActivity})."""
import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))))
from altention import testing as _testing  # noqa: E402
_testing.guard_home()  # tests never touch the real home (altention/testing.py)
import contextlib
import datetime
import importlib.util
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

ENGINE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def load(name):
    spec = importlib.util.spec_from_file_location("usage_" + name, os.path.join(ENGINE, "usage", name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


remote = load("remote")
collect = load("collect")
spend = load("spend")
PLUS8 = datetime.timezone(datetime.timedelta(hours=8))


class Zone:
    """A zone 8 hours ahead of UTC, as usage_zone's Zone answers."""
    def local(self, ts):
        return datetime.datetime.fromtimestamp(ts, PLUS8).replace(tzinfo=None)

    def offset_at(self, ts):
        return 8 * 3600


def row(session, when, tokens, msg="m", req="r", hour_cache=0, kind="assistant"):
    """One transcript row as Claude Code writes it (copied from a real one: usage beside cache_creation)."""
    return json.dumps({"type": kind, "sessionId": session, "timestamp": when, "requestId": req,
                       "message": {"id": msg, "model": "claude-x", "usage": {
                           "input_tokens": tokens, "output_tokens": 0, "cache_creation_input_tokens": hour_cache,
                           "cache_read_input_tokens": 0,
                           "cache_creation": {"ephemeral_1h_input_tokens": hour_cache, "ephemeral_5m_input_tokens": 0}}}})


class Home(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="usage spend ")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.home = mock.patch.dict(os.environ, {"HOME": self.tmp})
        self.home.start()
        self.addCleanup(self.home.stop)

    def transcript(self, rel, rows):
        path = os.path.join(self.tmp, ".claude", "projects", rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(rows) + "\n")
        return path


class Days(Home):
    def test_a_message_counts_once_on_its_day_in_the_zone(self):
        self.transcript("-w/s1.jsonl", [
            row("s1", "2026-10-20T15:00:00.000Z", 10, msg="a"),   # 23:00 there: the 20th
            row("s1", "2026-10-20T16:30:00.000Z", 30, msg="b"),   # 00:30 there: the 21st
            row("s1", "2026-10-20T16:30:01.000Z", 30, msg="b"),   # the same answer streamed again
            row("s1", "2026-10-20T16:31:00.000Z", 99, kind="user"),
            "not json {"])
        got = remote.claude_days(Zone(), 0)
        self.assertEqual(got, {"s1": {"2026-10-20": 10.0, "2026-10-21": 30.0}})

    def test_subagents_count_for_their_parent_and_hour_caches_cost_more(self):
        self.transcript("-w/s1.jsonl", [row("s1", "2026-10-20T01:00:00Z", 10, msg="a")])
        self.transcript("-w/s1/subagents/agent-x.jsonl", [row("s1", "2026-10-20T02:00:00Z", 0, msg="b", hour_cache=4)])
        got = remote.claude_days(Zone(), 0)
        self.assertEqual(got, {"s1": {"2026-10-20": 10 + 4 * 2.0}})

    def test_only_conversations_written_to_lately_all_of_their_days(self):
        old = self.transcript("-w/old.jsonl", [row("old", "2026-09-01T01:00:00Z", 10)])
        os.utime(old, (1000, 1000))
        self.transcript("-w/s1.jsonl", [row("s1", "2026-09-01T01:00:00Z", 10, msg="a"),
                                        row("s1", "2026-10-20T01:00:00Z", 5, msg="b")])
        sub = self.transcript("-w/s1/subagents/agent-y.jsonl", [row("s1", "2026-09-02T01:00:00Z", 2, msg="c")])
        os.utime(sub, (1000, 1000))  # an old subagent of a conversation still going: counted with it
        since = datetime.datetime(2026, 10, 1, tzinfo=datetime.timezone.utc).timestamp()
        self.assertEqual(remote.claude_days(Zone(), since),
                         {"s1": {"2026-09-01": 10.0, "2026-09-02": 2.0, "2026-10-20": 5.0}})


class Split(unittest.TestCase):
    def test_cost_over_its_days_then_scaled_to_the_day_totals(self):
        sessions = [{"period": "s1", "agent": "claude", "totalCost": 30.0},
                    {"period": "s2", "agent": "claude", "totalCost": 10.0}]
        weights = {"s1": {"2026-10-01": 1.0, "2026-10-20": 2.0}, "s2": {"2026-10-20": 1.0}}
        daily = [{"date": "2026-10-20", "agent": "claude", "cost": 27.0}, {"date": "2026-10-20", "agent": "codex", "cost": 5}]
        got = remote.split(sessions, weights, daily, Zone())
        self.assertAlmostEqual(got["s1"]["2026-10-01"], 10.0)  # no day total for it: as split
        self.assertAlmostEqual(got["s1"]["2026-10-20"], 20.0 * 27 / 30)
        self.assertAlmostEqual(got["s2"]["2026-10-20"], 10.0 * 27 / 30)

    def test_without_rows_on_the_day_last_active(self):
        sessions = [{"period": "c1", "agent": "codex", "totalCost": 4.0,
                     "metadata": {"lastActivity": "2026-10-20T16:30:00.000Z"}},
                    {"period": "wf_1", "agent": "claude", "totalCost": 2.0, "metadata": {}}]
        got = remote.split(sessions, {"c1": {"2026-10-01": 1.0}}, [], Zone())
        self.assertEqual(got, {"c1": {"2026-10-21": 4.0}, "wf_1": {"?": 2.0}})


class Main(Home):
    """remote.py's whole answer: sessions with 7 and 30 days, and projects of the 7 days only."""

    def run_main(self, days, today="2026-10-24T04:00:00Z"):
        now = remote.stamp_of(today)
        self.transcript("-w-a/long.jsonl", [
            row("long", "2026-10-02T01:00:00Z", 2, msg="a", req="1"), row("long", "2026-10-22T01:00:00Z", 1, msg="b"),
            row("long", "2026-10-24T01:00:00Z", 1, msg="c"),
            json.dumps({"type": "user", "cwd": "/w/a", "sessionId": "long"})])
        session = [{"period": "long", "agent": "claude", "totalCost": 40.0, "totalTokens": 4000,
                    "metadata": {"lastActivity": "2026-10-22T01:00:00.000Z"}}]

        def ccusage(*args, zone=None):
            if args == ("daily",):  # every day there is: the first one is where the cumulative starts
                return {"daily": [{"period": "2026-08-03"}, {"period": "2026-07-22"}]}
            return {"daily": []} if args[0] == "daily" else {"session": session}
        out = io.StringIO()
        zone = mock.Mock(zone=lambda: (Zone(), None, None))
        with mock.patch.object(remote, "ccusage", ccusage), mock.patch.object(remote.time, "time", lambda: now), \
                mock.patch.dict(sys.modules, {"usage_zone": zone}), contextlib.redirect_stdout(out):
            remote.main(days)
        return json.loads(out.getvalue())

    def test_a_long_conversation_counts_its_week_only(self):
        got = self.run_main(31)
        s = got["sessions"][0]
        self.assertEqual((s["id"], s["cwd"], s["project"]), ("long", "/w/a", "a"))
        self.assertAlmostEqual(s["cost1"], 10.0)    # 1 of its 4 parts was today (12:00 there)
        self.assertAlmostEqual(s["cost7"], 20.0)    # 2 of them in the last 7 days
        self.assertAlmostEqual(s["cost30"], 40.0)
        self.assertAlmostEqual(s["costAll"], 40.0)
        self.assertEqual((s["tokens1"], s["tokens7"], s["tokens30"], s["tokensAll"]), (1000, 2000, 4000, 4000))
        self.assertEqual(len(got["projects"]), 1)
        self.assertAlmostEqual(got["projects"][0]["cost"], 20.0)  # was 40: everything it ever cost
        self.assertEqual(got["since"], "2026-07-22")

    def test_fewer_than_30_days_has_no_30(self):
        s = self.run_main(8)["sessions"][0]
        self.assertIsNone(s["cost30"])
        self.assertIsNone(s["tokens30"])
        self.assertAlmostEqual(s["cost7"], 20.0)
        self.assertAlmostEqual(s["costAll"], 40.0)


class Saved(Home):
    def test_each_machine_as_it_answered(self):
        results = [{"machine": "lap", "daily": [], "projects": [], "sessions": [{"id": "s1", "cost7": 1.0}],
                    "since": "2026-07-22"},
                   {"machine": "box", "error": "連不上（經 box）"},
                   {"machine": "old", "daily": [], "projects": []}]  # a remote.py from before C-98
        collect.save_spend(results, "2026-10-24", now=100.0)
        path = collect.spend_path()
        self.assertTrue(path.startswith(self.tmp))
        with open(path, encoding="utf-8") as f:
            got = json.load(f)
        self.assertEqual(got, {"v": 1, "at": 100.0, "today": "2026-10-24", "machines": [
            {"machine": "lap", "sessions": [{"id": "s1", "cost7": 1.0}], "since": "2026-07-22", "daily": []},
            {"machine": "box", "error": "連不上（經 box）"}, {"machine": "old", "old": True}]})
        self.assertEqual(os.stat(os.path.dirname(path)).st_mode & 0o777, 0o700)
        self.assertFalse(os.path.exists(path + ".tmp"))

    def test_a_folder_it_cannot_write_is_said_not_raised(self):
        os.makedirs(os.path.join(self.tmp, ".local", "share"))
        with open(os.path.join(self.tmp, ".local", "share", "usage"), "w") as f:
            f.write("a file where the folder goes")
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            collect.save_spend([], "2026-10-24")
        self.assertIn("spend.json 寫不了", err.getvalue())


def conversation(sid, cwd="/w/a", **more):
    return dict({"kind": "conversation", "cwd": cwd, "session_id": sid}, **more)


def session(sid, c7, c30, cwd="/w/a", agent="claude", c1=0.0, call=None):
    """One of remote.py's sessions; tokens are 100 a dollar."""
    call = (c30 if c30 is not None else c7) if call is None else call
    toks = lambda c: None if c is None else int(c * 100)
    return {"agent": agent, "id": sid, "cwd": cwd, "project": os.path.basename(cwd), "cost1": c1, "cost7": c7,
            "cost30": c30, "costAll": call, "tokens1": toks(c1), "tokens7": toks(c7), "tokens30": toks(c30),
            "tokensAll": toks(call), "last": "2026-10-23T01:00:00.000Z"}


class Owners(unittest.TestCase):
    def test_current_history_and_turns_on_their_machines(self):
        channels = {
            "11": conversation("now1", history=["old1"], turns={
                "t1": ["/h/.claude/projects/-w-a/turn1.jsonl", 5, None, None],
                "t2": ["/h/.claude/projects/-w-a/far1.jsonl", 5, None, None]}, turn_hosts={"t2": "box"}),
            "12": conversation("now2", pane={"host": "box", "id": "%1"}, history=["now1"]),
            "13": {"kind": "board", "session_id": "nope"}}
        got = spend.owners(channels, "lap")
        self.assertEqual(got, {("lap", "now1"): "11", ("lap", "old1"): "11", ("lap", "turn1"): "11",
                               ("box", "far1"): "11", ("box", "now2"): "12", ("box", "now1"): "12"})

    def test_a_current_conversation_wins_over_one_held_before(self):
        channels = {"11": conversation("x1", history=["s"]), "12": conversation("s")}
        self.assertEqual(spend.owners(channels, "lap")[("lap", "s")], "12")

    def test_odd_entries_are_skipped(self):
        channels = {"11": "not a dict", "12": {"kind": "conversation", "history": "x", "turns": [1], "pane": 3}}
        self.assertEqual(spend.owners(channels, "lap"), {})


class Report(Home):
    def write(self, machines):
        results = [dict(m, daily=[], projects=[]) if "error" not in m else m for m in machines]
        collect.save_spend(results, "2026-10-24", now=1000.0)

    def run_spend(self, *argv, channels=None, here="lap"):
        state = os.path.join(self.tmp, "state")
        if channels is not None:
            os.makedirs(state, exist_ok=True)
            with open(os.path.join(state, "channels.json"), "w") as f:
                json.dump(channels, f)
        out = io.StringIO()
        with mock.patch.object(spend, "bot_state", lambda: state), mock.patch.object(spend, "this_machine", lambda: here), \
                contextlib.redirect_stdout(out):
            code = spend.main(list(argv))
        return code, out.getvalue()

    def test_folders_channels_and_what_no_channel_holds(self):
        self.write([{"machine": "lap", "since": "2026-07-22", "sessions": [
                        session("a1", 2.0, 5.0, c1=1.0, call=9.0), session("a2", 1.0, 1.0),
                        session("b1", 4.0, 4.0, cwd="/w/b"), session("t1", 0.5, 0.5, cwd="/w/b")]},
                    {"machine": "pad", "since": "2026-09-01", "sessions": [session("p1", 0.0, 0.0, call=2.0)]},
                    {"machine": "box", "error": "連不上（經 box）"}, {"machine": "old", "sessions": None}])
        code, out = self.run_spend("--json", channels={"11": conversation("a1", history=["a2"]), "12": conversation("b1")})
        self.assertEqual(code, 0)
        got = json.loads(out)
        self.assertEqual((got["v"], got["at"], got["windows"]), (1, 1000.0, ["1", "7", "30", "all"]))
        self.assertEqual((got["since"], got["since_all"]), ({"lap": "2026-07-22", "pad": "2026-09-01"}, "2026-09-01"))
        self.assertEqual(got["machines"], [{"machine": "lap"}, {"machine": "pad"},
                                           {"machine": "box", "error": "連不上（經 box）"}, {"machine": "old", "old": True}])
        self.assertEqual([(f["cwd"], f["machine"], f["cost"]) for f in got["folders"]],
                         [("/w/b", "lap", {"1": 0.0, "7": 4.5, "30": 4.5, "all": 4.5}),
                          ("/w/a", "lap", {"1": 1.0, "7": 3.0, "30": 6.0, "all": 10.0}),
                          ("/w/a", "pad", {"1": 0.0, "7": 0.0, "30": 0.0, "all": 2.0})])
        self.assertEqual([(c["channel"], c["sessions"], c["cost"], c["machines"]) for c in got["channels"]],
                         [("12", 1, {"1": 0.0, "7": 4.0, "30": 4.0, "all": 4.0}, ["lap"]),
                          ("11", 2, {"1": 1.0, "7": 3.0, "30": 6.0, "all": 10.0}, ["lap"])])
        self.assertEqual(got["unmatched"]["cost"], {"1": 0.0, "7": 0.5, "30": 0.5, "all": 2.5})
        self.assertEqual(got["unmatched"]["tokens"], {"1": 0, "7": 50, "30": 50, "all": 250})

    def test_without_the_bot_here_no_channels_and_why(self):
        self.write([{"machine": "lap", "sessions": [session("a1", 2.0, 5.0)]}])
        got = json.loads(self.run_spend("--json")[1])
        self.assertIsNone(got["channels"])
        self.assertIn("機器人在別台", got["channels_why"])
        self.assertEqual(got["unmatched"]["cost"], {"1": 0.0, "7": 2.0, "30": 5.0, "all": 5.0})

    def test_an_unreadable_channels_file_is_said_so(self):
        self.write([{"machine": "lap", "sessions": [session("a1", 2.0, 5.0)]}])
        os.makedirs(os.path.join(self.tmp, "state"))
        with open(os.path.join(self.tmp, "state", "channels.json"), "w") as f:
            f.write("{ half")
        got = json.loads(self.run_spend("--json")[1])
        self.assertIsNone(got["channels"])
        self.assertIn("讀不出來", got["channels_why"])

    def test_a_machine_with_fewer_days_has_no_30(self):
        self.write([{"machine": "lap", "sessions": [session("a1", 2.0, None), session("a2", 1.0, 3.0)]}])
        got = json.loads(self.run_spend("--json", channels={})[1])
        self.assertEqual(got["folders"][0]["cost"], {"1": 0.0, "7": 3.0, "30": None, "all": 5.0})

    def test_no_data_yet(self):
        code, out = self.run_spend("--json")
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(out)["v"], 1)
        self.assertIn("還沒有花費的資料", json.loads(out)["error"])
        code, out = self.run_spend()
        self.assertEqual(code, 1)
        self.assertIn("usage 插件每小時收一次", out)

    def test_as_text(self):
        self.write([{"machine": "lap", "since": "2026-07-22", "sessions": [session("a1", 2.0, 5.0, c1=0.5, call=8.0),
                                                                           session("z", 1.0, 1.0)]},
                    {"machine": "old", "sessions": None}])
        with mock.patch.object(spend.time, "time", lambda: 1000.0 + 600):
            code, out = self.run_spend(channels={"11": conversation("a1")})
        self.assertEqual(code, 0)
        self.assertIn("資料是 10 分鐘前收的", out)
        self.assertIn("old 的引擎是舊版", out)
        self.assertIn("累計：從 2026-07-22 起每台都算在內（各台的資料最早到：lap 2026-07-22）", out)
        self.assertIn("各資料夾（今天／近 7 天／近 30 天／累計）", out)
        self.assertIn("- /w/a（lap）：$0.50／$3.00／$6.00／$9.00", out)
        self.assertIn("- <#11>（lap）：$0.50／$2.00／$5.00／$8.00", out)
        self.assertIn("對不到頻道的（終端機直接開的、別的工具）：$0.00／$1.00／$1.00／$1.00", out)

    def test_wrong_usage(self):
        self.assertEqual(self.run_spend("--what")[0], 2)


class Limits(Home):
    """The subscription's limits beside the spend, from the last sample (#activity-manager 10-24)."""
    def sample(self, *rows):
        os.makedirs(os.path.join(self.tmp, ".local", "share", "usage"), exist_ok=True)
        with open(spend.samples_path(), "w") as f:
            f.write("".join(json.dumps(r) + "\n" for r in rows) + "half a line {")

    def test_the_reset_times_come_from_the_stream(self):
        with open(os.path.join(ENGINE, "usage", "samples", "2.1.299-stream.jsonl"), encoding="utf-8") as f:
            limits, why = collect.limits_from_stream(f.read(), datetime.datetime(2026, 10, 14, 16, 40))
        self.assertIsNone(why)
        self.assertEqual(sorted(collect.RESETS), ["five_hour", "seven_day"])
        self.assertTrue(all(isinstance(v, (int, float)) for v in collect.RESETS.values()))
        collect.limits_from_stream("nothing", datetime.datetime(2026, 10, 14, 16, 40))
        self.assertEqual(collect.RESETS, {}, "a failed read leaves none")

    def test_the_last_sample_with_its_resets(self):
        self.sample({"t": 1.0, "limits": []},
                    {"t": 2.0, "limits": [["Current session", 26, "7pm"], ["Current week (all models)", 86, "Oct 26, 5am"]],
                     "resets": {"five_hour": 100.0, "seven_day": 200}})
        got = spend.limits(spend.last_sample(), False)
        self.assertEqual(got, ({"at": 2.0, "session": {"used": 26, "resets_at": 100.0},
                                "week": {"used": 86, "resets_at": 200}}, None))

    def test_an_older_sample_has_no_reset_times(self):
        self.sample({"t": 2.0, "limits": [["Current week (all models)", 104, "Oct 26, 5am"]]})
        self.assertEqual(spend.limits(spend.last_sample(), False),
                         ({"at": 2.0, "session": None, "week": {"used": 104, "resets_at": None}}, None))

    def test_the_titles_are_collects(self):
        with mock.patch.object(collect, "WINDOWS", (("five_hour", "Session now"), ("seven_day", "Week now"))), \
                mock.patch.dict(sys.modules, {"collect": collect}):
            got = spend.limits({"t": 1.0, "limits": [["Week now", 7, "x"]]}, False)
        self.assertEqual(got[0]["week"], {"used": 7, "resets_at": None})

    def test_why_none(self):
        self.assertEqual(spend.limits(None, False), (None, "none"))
        self.assertEqual(spend.limits({"t": 1.0, "limits": []}, False), (None, "unread"))
        self.assertEqual(spend.limits({"t": 1.0, "limits": [["Current session", 1, "x"]]}, True), (None, "off"))

    def test_in_the_json(self):
        collect.save_spend([{"machine": "lap", "sessions": []}], "2026-10-24", now=1.0)
        self.sample({"t": 2.0, "limits": [["Current week (all models)", 50, "x"]], "resets": {"seven_day": 9}})
        out = io.StringIO()
        with mock.patch.object(spend, "bot_state", lambda: os.path.join(self.tmp, "none")), \
                mock.patch.object(spend, "limits_off", lambda: False), contextlib.redirect_stdout(out):
            spend.main(["--json"])
        got = json.loads(out.getvalue())
        self.assertEqual(got["limits"]["week"], {"used": 50, "resets_at": 9})
        self.assertNotIn("limits_why", got)


class Version2(Home):
    """usage.spend v2 (#activity-dev-2 11-03): --json --v 2 adds each day's spend per machine, the limits' hourly
    history and the plan, for Activity's trend chart (it read chart-data.json, which is no interface); without
    --v 2 the answer is v1 as before."""
    TODAY = "2026-10-24"

    def save(self, results):
        collect.save_spend(results, self.TODAY, now=1000.0)

    def sample(self, *rows):
        os.makedirs(os.path.join(self.tmp, ".local", "share", "usage"), exist_ok=True)
        with open(spend.samples_path(), "w") as f:
            f.write("".join(json.dumps(r) + "\n" for r in rows) + "half a line {")

    def run_json(self, *more, off=False, plan=None, now=None):
        out = io.StringIO()
        with mock.patch.object(spend, "bot_state", lambda: os.path.join(self.tmp, "none")), \
                mock.patch.object(spend, "limits_off", lambda: off), \
                mock.patch.object(spend, "plan", lambda: plan), \
                mock.patch.object(spend.time, "time", lambda: now or 1793000000.0), contextlib.redirect_stdout(out):
            code = spend.main(["--json"] + list(more))
        return code, json.loads(out.getvalue())

    def daily(self, *rows):
        return [{"date": d, "agent": a, "cost": c, "tokens": 1} for d, a, c in rows]

    def test_collect_keeps_a_days_dollars_every_agent_together(self):
        got = collect.daily_kept(self.daily(("2026-10-24", "claude", 1.5), ("2026-10-24", "codex", 0.25),
                                            ("2026-10-20", "claude", 2.0), ("2026-09-01", "claude", 9.0),
                                            ("2026-10-25", "claude", 3.0)), self.TODAY)
        self.assertEqual(got, [["2026-10-20", 2.0], ["2026-10-24", 1.75]], "the last 35 days, none after today")
        self.assertEqual(collect.daily_kept([{"date": "x"}, "odd"], "not a day"), [])

    def test_days_per_machine_oldest_first(self):
        self.save([{"machine": "lap", "sessions": [], "since": "2026-10-01",
                    "daily": self.daily(("2026-10-24", "claude", 1.5), ("2026-10-23", "claude", 2.0))},
                   {"machine": "box", "sessions": [], "since": "2026-10-23", "daily": self.daily(("2026-10-24", "claude", 4.0))},
                   {"machine": "far", "error": "連不上"}])
        code, d = self.run_json("--v", "2")
        self.assertEqual((code, d["v"]), (0, 2))
        self.assertNotIn("days_why", d)
        self.assertEqual(len(d["days"]), 30)
        self.assertEqual((d["days"][0]["date"], d["days"][-1]["date"]), ("2026-09-25", self.TODAY))
        self.assertEqual(d["days"][-1]["machines"], {"lap": 1.5, "box": 4.0})
        self.assertEqual(d["days"][-2]["machines"], {"lap": 2.0, "box": 0.0}, "a day it spent nothing: 0")
        self.assertEqual(d["days"][-3]["machines"], {"lap": 0.0}, "before box's since: left out")
        self.assertEqual(d["days"][0]["machines"], {}, "before every since")
        self.assertTrue(all("far" not in x["machines"] for x in d["days"]), "a machine that did not answer")
        for key in ("folders", "channels_why", "since", "since_all", "limits_why"):
            self.assertIn(key, d, "everything v1 has")

    def test_data_from_before_v2_says_old(self):
        path = collect.spend_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            json.dump({"v": 1, "at": 1.0, "today": self.TODAY, "machines": [{"machine": "lap", "sessions": [], "since": "2026-10-01"}]}, f)
        _, d = self.run_json("--v", "2")
        self.assertEqual((d["days"], d["days_why"]), (None, "old"))

    def test_v1_stays_word_for_word(self):
        self.save([{"machine": "lap", "sessions": [], "since": "2026-10-01", "daily": self.daily(("2026-10-24", "claude", 1.5))}])
        self.sample({"t": 1793000000.0, "limits": [["Current week (all models)", 50, "x"]]})
        _, d = self.run_json(plan={"name": "Max", "pro": 5})
        self.assertEqual(d["v"], 1)
        for key in ("days", "days_why", "limits_history", "plan"):
            self.assertNotIn(key, d)

    def test_the_limits_history_and_the_plan(self):
        now = 1793000000.0
        self.save([{"machine": "lap", "sessions": [], "since": "2026-10-01", "daily": []}])
        self.sample({"t": now - 29 * 86400, "limits": [["Current week (all models)", 1, "x"]]},
                    {"t": now - 3600, "limits": [["Current week (all models)", 40, "x"], ["Current session", 7, "y"]]},
                    {"t": now - 7200, "limits": [["Current week (all models)", 30, "x"]]},
                    {"t": now - 60, "limits": []}, {"t": "odd"}, "not a sample")
        _, d = self.run_json("--v", "2", plan={"name": "Max", "pro": 5}, now=now)
        self.assertEqual(d["limits_history"], {"week": [[int(now - 7200), 30], [int(now - 3600), 40]],
                                               "session": [[int(now - 3600), 7]]}, "28 days, by time; older left out")
        self.assertEqual(d["plan"], {"name": "Max", "pro": 5})
        _, d = self.run_json("--v", "2", off=True)
        self.assertEqual((d["limits_history"], d["plan"]), (None, None))
        with mock.patch.object(spend, "HISTORY_MAX", 1):
            _, d = self.run_json("--v", "2", now=now)
        self.assertEqual(d["limits_history"]["week"], [[int(now - 3600), 40]], "the newest")

    def test_the_plan_from_the_setting(self):
        with mock.patch("altention.config.setting", lambda key, default=None: ["Ultra", 10]):
            self.assertEqual(spend.plan(), {"name": "Ultra", "pro": 10})
        for bad in (None, ["Ultra"], ["Ultra", 0], ["Ultra", True], "Ultra"):
            with mock.patch("altention.config.setting", lambda key, default=None, v=bad: v):
                self.assertIsNone(spend.plan(), bad)

    def test_wrong_usage_and_no_data(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(spend.main(["--v", "2"]), 2, "--v 2 is for --json")
            self.assertEqual(spend.main(["--json", "--v", "3"]), 2)
        code, d = self.run_json("--v", "2")
        self.assertEqual((code, d["v"], d["at"]), (1, 2, None))
        self.assertIn("error", d)


if __name__ == "__main__":
    unittest.main()
