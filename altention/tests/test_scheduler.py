"""altention.scheduler: schedule files, what is due (sleep, summer time), the state per machine, the log."""
import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))))
from altention import testing as _testing  # noqa: E402
_testing.guard_home()  # tests never touch the real home (altention/testing.py)
_os.environ["ALT_RUNTIME"] = _os.path.join(_os.environ["HOME"], "runtime")  # nor /tmp/altention-<uid>
import calendar
import datetime
import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

from altention import scheduler as s

try:
    import zoneinfo
except ImportError:  # Python 3.8
    zoneinfo = None

TAIPEI = s.zone("Asia/Taipei")
BERLIN = s.zone("Europe/Berlin")      # summer time: 2026-03-29 02:00 -> 03:00, 2026-10-25 03:00 -> 02:00
NEW_YORK = s.zone("America/New_York")


def at(tz, text):
    """The instant of a wall-clock time "2026-10-08 09:00" in a zone."""
    return tz.resolve(datetime.datetime.strptime(text, "%Y-%m-%d %H:%M"))


def utc(text):
    return calendar.timegm(datetime.datetime.strptime(text, "%Y-%m-%d %H:%M").timetuple())


def item(when, do=None, **more):
    raw = {"name": "x", "when": when, "do": do or {"message": "hi"}}
    raw.update(more)
    return s.parse({"items": [raw]})["items"][0]


def zone_setting(*value, **kw):
    """The instance's settings saying core.timezone (no value: an instance without that setting)."""
    settings = kw["settings"] if "settings" in kw else {"core": {"timezone": value[0]}} if value else {"core": {"language": "zh-TW"}}
    return mock.patch.object(s.config, "settings", return_value=settings)


def config_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def write(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(data if isinstance(data, str) else json.dumps(data, ensure_ascii=False))
    return path



def done(slot):
    """A note of an item served at `slot`, as read_state() gives it."""
    return {"slot": slot, "state": "done"}

class Zones(unittest.TestCase):
    def test_offsets_and_wall_clock(self):
        self.assertEqual(TAIPEI.offset_at(utc("2026-10-06 00:00")), 8 * 3600)
        self.assertEqual(BERLIN.offset_at(utc("2026-01-15 12:00")), 3600)
        self.assertEqual(BERLIN.offset_at(utc("2026-07-15 12:00")), 7200)
        self.assertEqual(BERLIN.offset_at(utc("2026-03-29 00:59")), 3600)
        self.assertEqual(BERLIN.offset_at(utc("2026-03-29 01:00")), 7200)
        self.assertEqual(TAIPEI.local(utc("2026-10-06 00:30")), datetime.datetime(2026, 10, 6, 8, 30))
        self.assertEqual(TAIPEI.iso(utc("2026-10-06 00:30")), "2026-10-06T08:30:00+08:00")
        self.assertEqual(NEW_YORK.iso(utc("2026-01-06 12:00")), "2026-01-06T07:00:00-05:00")

    def test_resolve_ordinary_skipped_and_repeated_times(self):
        self.assertEqual(at(TAIPEI, "2026-10-08 09:00"), utc("2026-10-08 01:00"))
        self.assertEqual(at(BERLIN, "2026-03-29 02:30"), utc("2026-03-29 01:00"))   # skipped: 03:00 summer time
        self.assertEqual(BERLIN.local(at(BERLIN, "2026-03-29 02:30")), datetime.datetime(2026, 3, 29, 3, 0))
        self.assertEqual(at(BERLIN, "2026-10-25 02:30"), utc("2026-10-25 00:30"))   # twice: the first
        self.assertEqual(at(BERLIN, "2026-10-25 03:00"), utc("2026-10-25 02:00"))

    def test_the_rule_at_the_end_of_the_file_agrees_with_the_listed_changes(self):
        for name in ("Europe/Berlin", "America/New_York", "Australia/Sydney", "Asia/Taipei"):
            full = s.zone(name)
            for folder in s.zone_dirs():
                if os.path.exists(os.path.join(folder, name)):
                    with open(os.path.join(folder, name), "rb") as f:
                        rule_only = s.FileZone(name, f.read(), listed=False)
                    break
            t = utc("2010-01-01 00:00")
            while t < utc("2037-01-01 00:00"):
                self.assertEqual(rule_only.offset_at(t), full.offset_at(t), "%s %d" % (name, t))
                t += 5 * 3600 + 1800

    def test_rule_forms(self):
        fixed = s._Rule("<+0530>-5:30")
        self.assertEqual((fixed.offset_at(0), fixed.offset_at(utc("2026-07-01 00:00"))), (19800, 19800))
        dublin = s._Rule("IST-1GMT0,M10.5.0,M3.5.0/1")  # the "summer" offset is the smaller one
        self.assertEqual((dublin.offset_at(utc("2026-07-01 12:00")), dublin.offset_at(utc("2026-12-01 12:00"))), (3600, 0))
        julian = s._Rule("AAA0BBB,J60/0,J300/0")  # 1 March in every year
        self.assertEqual(julian.offset_at(utc("2028-02-29 23:00")), 0)
        self.assertEqual(julian.offset_at(utc("2028-03-01 00:00")), 3600)
        zero_based = s._Rule("AAA0BBB,59/0,300/0")  # day 59 is 29 February in a leap year
        self.assertEqual(zero_based.offset_at(utc("2028-02-29 00:00")), 3600)
        self.assertRaises(ValueError, s._Rule, "nonsense")

    @unittest.skipUnless(zoneinfo, "no zoneinfo to compare with (Python 3.8)")
    def test_same_answers_as_zoneinfo(self):
        for name in ("Asia/Taipei", "Europe/Berlin", "America/New_York", "Australia/Sydney", "Asia/Kolkata"):
            mine, theirs = s.zone(name), zoneinfo.ZoneInfo(name)
            t = utc("1990-01-01 00:00")
            while t < utc("2060-01-01 00:00"):
                want = datetime.datetime.fromtimestamp(t, theirs).utcoffset().total_seconds()
                self.assertEqual(mine.offset_at(t), want, "%s %d" % (name, t))
                t += 7 * 3600 + 1260

    def test_unknown_names_and_the_setting(self):
        for bad in ("Nowhere/Never", "../etc/passwd", "", None, "Asia/Tai pei"):
            self.assertRaises(s.ScheduleError, s.zone, bad)

    def test_zone_from_the_setting_or_the_stated_default(self):
        for settings in ({}, {"core": {}}, {"core": {"language": "zh-TW"}}, {"other": 1}):   # no such key
            with zone_setting(settings=settings):
                zone, source, problem = s.timezone()
            self.assertIsInstance(zone, s.SystemZone, settings)
            self.assertEqual((source, problem), ("system-default", None))
        with zone_setting("Europe/Berlin"):
            zone, source, problem = s.timezone()
            self.assertEqual((zone.name, source, problem), ("Europe/Berlin", "setting", None))

    def test_a_setting_that_cannot_be_used_gives_no_zone(self):
        for value in ("Nowhere/Never", "Asia/Taipeii", "", None, 8, ["Asia/Taipei"]):
            with zone_setting(value):
                zone, source, problem = s.timezone()
            self.assertEqual((zone, source), (None, "setting"), repr(value))
            self.assertIn("core.timezone", problem)
        for core in ([], "x", 3, None):   # core is there and is not an object: broken settings, not "no setting"
            with zone_setting(settings={"core": core}):
                zone, source, problem = s.timezone()
            self.assertEqual((zone, source), (None, "setting"), repr(core))
            self.assertIn("core", problem)

    def test_settings_that_cannot_be_read_give_no_zone_at_all(self):
        with mock.patch.object(s.config, "settings", side_effect=s.config.ConfigError("plugins.json 讀不出來")):
            zone, source, problem = s.timezone()
        self.assertEqual((zone, source), (None, "setting"))
        self.assertIn("plugins.json 讀不出來", problem)

    def test_a_damaged_zone_file_is_a_zone_this_machine_does_not_have(self):
        with open(os.path.join("/usr/share/zoneinfo", "Europe/Berlin"), "rb") as f:
            good = f.read()
        folder = tempfile.mkdtemp()
        os.makedirs(os.path.join(folder, "Europe"))
        import random
        rng, refused = random.Random(7), 0
        with mock.patch.dict(os.environ, {"TZDIR": folder}), mock.patch.object(
                s, "zone_dirs", return_value=[folder]):
            for n in range(400):
                data = bytearray(good)
                for _ in range(rng.randint(1, 40)):
                    data[rng.randrange(len(data))] = rng.randrange(256)
                if n % 5 == 0:
                    data = data[:rng.randrange(len(data))]
                with open(os.path.join(folder, "Europe", "Berlin"), "wb") as f:
                    f.write(bytes(data))
                try:
                    got = s.zone("Europe/Berlin")
                except s.ScheduleError:
                    refused += 1
                    continue
                for ts in (utc("2026-01-01 00:00"), utc("2026-07-01 00:00"), utc("2027-07-01 00:00")):
                    got.offset_at(ts)      # a zone that was given out answers without raising
                    got.local(ts)
        self.assertGreater(refused, 0)

    def test_system_zone_follows_the_machine(self):
        now = utc("2026-10-06 00:00")
        self.assertEqual(s.SystemZone().offset_at(now), __import__("time").localtime(now).tm_gmtoff)


class Files(unittest.TestCase):
    def test_new_form(self):
        got = s.parse({"late": "1h", "future-field": 1, "items": [
            {"name": "morning", "when": {"at": "08:05", "days": ["thu", "mon"]}, "do": {"message": "早", "channel": "project-a"}},
            {"name": "collect", "enabled": False, "late": "20m", "when": {"every": "10m", "offset": "7m"},
             "do": {"run": "tools/collect --all", "output": "post", "timeout": "90s", "env": {"A": "b"}}, "x": 1},
            {"name": "review", "when": {"once": "2026-10-08 09:00"}, "do": {"message": "看"}}]})
        a, b, c = got["items"]
        self.assertEqual((a["kind"], a["at"], a["days"], a["late"], a["enabled"]), ("at", 485, [0, 3], 3600, True))
        self.assertEqual(a["do"], {"message": "早", "channel": "project-a", "log": None, "retry": False})
        self.assertEqual((a["mark"], b["mark"], c["mark"]), ("at 485 mon,thu", "every 600 420", "once 2026-10-08 09:00"))
        self.assertEqual((b["kind"], b["every"], b["offset"], b["late"], b["enabled"]), ("every", 600, 420, 1200, False))
        self.assertEqual(b["do"], {"run": "tools/collect --all", "output": "post", "timeout": 90, "env": {"A": "b"}, "log": None,
                                   "retry": False})
        self.assertEqual((c["kind"], c["once"], c["do"]["channel"]), ("once", datetime.datetime(2026, 10, 8, 9, 0), None))

    def test_defaults_for_late(self):
        self.assertEqual(item({"at": "08:00"})["late"], 3 * 3600)
        self.assertEqual(item({"once": "2026-10-08 09:00"})["late"], 3 * 3600)
        self.assertEqual(item({"every": "15m"})["late"], 900)
        self.assertEqual(item({"every": "15m"}, late="2h")["late"], 7200)

    def test_older_form_with_the_channel_on_top(self):
        got = s.parse({"channel": 100000000000000001, "items": [
            {"name": "lesson", "enabled": True, "at": "00:30", "days": "daily", "text": "上課"},
            {"name": "weekly", "at": "20:30", "days": ["sun"], "text": "小結"}]})
        self.assertEqual(got["channel"], "100000000000000001")
        self.assertEqual([(i["kind"], i["at"], i["days"], i["do"]) for i in got["items"]],
                         [("at", 30, None, {"message": "上課", "channel": "100000000000000001", "log": None, "retry": False}),
                          ("at", 1230, [6], {"message": "小結", "channel": "100000000000000001", "log": None, "retry": False})])
        mixed = s.parse({"channel": "project-a", "items": [{"name": "n", "when": {"at": "08:00"}, "do": {"message": "m"}}]})
        self.assertEqual(mixed["items"][0]["do"]["channel"], "project-a")

    def test_what_is_wrong_is_said_with_the_item(self):
        ok_do = {"message": "m"}
        cases = [
            (3, "items"), ({"items": "x"}, "items"), ({"items": [3]}, "第 1 項"),
            ({"items": [{"when": {"at": "08:00"}, "do": ok_do}]}, "name"),
            ({"items": [{"name": "a", "when": {"at": "8pm"}, "do": ok_do}]}, "「a」"),
            ({"items": [{"name": "a", "when": {"at": "24:00"}, "do": ok_do}]}, "HH:MM"),
            ({"items": [{"name": "a", "when": {"at": "08:00", "days": ["funday"]}, "do": ok_do}]}, "days"),
            ({"items": [{"name": "a", "when": {"at": "08:00", "every": "1h"}, "do": ok_do}]}, "只能一個"),
            ({"items": [{"name": "a", "when": {}, "do": ok_do}]}, "at、every、once"),
            ({"items": [{"name": "a", "when": {"every": "30s"}, "do": ok_do}]}, "時間長度"),
            ({"items": [{"name": "a", "when": {"every": "25h"}, "do": ok_do}]}, "1m 到 24h"),
            ({"items": [{"name": "a", "when": {"every": "10m", "offset": "10m"}, "do": ok_do}]}, "offset"),
            ({"items": [{"name": "a", "when": {"once": "tomorrow"}, "do": ok_do}]}, "once"),
            ({"items": [{"name": "a", "when": {"at": "08:00"}}]}, "do"),
            ({"items": [{"name": "a", "when": {"at": "08:00"}, "do": {"message": "m", "run": "r"}}]}, "只能一個"),
            ({"items": [{"name": "a", "when": {"at": "08:00"}, "do": {"message": " "}}]}, "空"),
            ({"items": [{"name": "a", "when": {"at": "08:00"}, "do": {"run": "r", "output": "board"}}]}, "output"),
            ({"items": [{"name": "a", "when": {"at": "08:00"}, "do": {"run": "r", "timeout": -1}}]}, "timeout"),
            ({"items": [{"name": "a", "when": {"at": "08:00"}, "do": {"run": "r", "env": {"A": 1}}}]}, "env"),
            ({"items": [{"name": "a", "when": {"at": "08:00"}, "do": ok_do, "late": "8d"}]}, "時間長度"),
            ({"items": [{"name": "a", "when": {"at": "08:00"}, "do": ok_do, "late": "200h"}]}, "late"),
            ({"items": [{"name": "a", "at": "08:00"}]}, "text"),
            ([{"name": "a", "when": {"at": "08:00"}, "do": ok_do, "enabled": 0}], "enabled"),
            ([{"name": "a", "when": {"at": "08:00"}, "do": ok_do, "enabled": None}], "enabled"),
            ([{"name": "a", "when": {"at": "08:00"}, "do": ok_do, "enabled": "false"}], "enabled"),
            ([{"name": "a", "at": "08:00", "text": "t", "enabled": 1}], "enabled"),
            ([{"name": "a", "when": {"at": "08:00"}, "do": {"run": "r", "retry": "yes"}}], "retry"),
            ({"items": [{"name": "a", "when": {"at": "08:00"}, "do": ok_do, "timezone": "Mars/Base"}]}, "timezone"),
            ({"items": [{"name": "a", "when": {"at": "08:00"}, "do": {"message": "m", "log": 3}}]}, "log"),
            ({"items": [{"name": "a", "when": {"at": "08:00", "offset": "machine"}, "do": ok_do, "late": "x"}]}, "時間長度"),
            ([{"name": "a", "when": {"every": "10m", "offset": "sometimes"}, "do": ok_do}], "時間長度"),
            ({"items": [{"name": "a", "when": {"at": "08:00"}, "do": ok_do},
                        {"name": "a", "when": {"at": "09:00"}, "do": ok_do}]}, "重複"),
        ]
        for data, expect in cases:
            with self.assertRaises(s.ScheduleError, msg=repr(data)) as c:
                s.parse(data)
            self.assertIn(expect, str(c.exception), repr(data))

    def test_sync_and_update_are_refused_unless_kept_out_of_the_scheduler(self):
        sync = {"name": "sync", "when": {"every": "15m"}, "do": {"run": "x sync"}}
        asked = []

        def forbidden(raw):   # stands for altention.plugin.forbidden_run
            asked.append(raw["name"])
            return "sync" in raw["do"].get("run", "") and raw.get("scheduler", True) is not False
        with mock.patch.object(s.plugin, "forbidden_run", forbidden, create=True):
            with self.assertRaises(s.ScheduleError) as c:
                s.parse([sync])
            self.assertIn("「sync」", str(c.exception))
            self.assertIn("不進排程器", str(c.exception))
            kept = s.parse([dict(sync, scheduler=False, why="stays in cron"),
                            {"name": "other", "when": {"at": "08:00"}, "do": {"message": "m"}}])["items"]
            self.assertEqual([(i["name"], i["scheduler"]) for i in kept], [("sync", False), ("other", True)])
            self.assertEqual(asked, ["sync", "sync", "other"])
            self.assertIn("留在 cron，排程器不接手", s.describe(kept[0]))
            self.assertNotIn("留在 cron", s.describe(kept[1]))
        self.assertRaises(s.ScheduleError, s.parse, [dict(sync, scheduler="no")])

    def test_retry_and_the_mark_of_a_schedule(self):
        self.assertTrue(item({"at": "08:00"}, {"run": "r", "retry": True})["do"]["retry"])
        self.assertFalse(item({"at": "08:00"}, {"run": "r"})["do"]["retry"])
        self.assertEqual(item({"at": "08:00"}, timezone="machine")["mark"], "at 480 daily machine")
        self.assertNotEqual(item({"at": "08:00"})["mark"], item({"at": "08:01"})["mark"])
        self.assertEqual(item({"at": "08:00"})["mark"], item({"at": "08:00"}, {"run": "other"}, late="1h")["mark"])

    def test_a_bare_list_is_a_schedule(self):
        items = [{"name": "n", "when": {"at": "08:00"}, "do": {"message": "m"}}]
        self.assertEqual(s.parse(items), s.parse({"items": items}))
        self.assertEqual(s.parse([]), {"channel": None, "items": []})
        folder = tempfile.mkdtemp()
        self.assertEqual(s.load(write(os.path.join(folder, "清 單.json"), items))["items"][0]["name"], "n")

    def test_offset_by_machine_comes_from_the_stable_name(self):
        def minute(name, every_minutes):  # md5 of the name, modulo 60, modulo the interval's minutes
            return int(hashlib.md5(name.encode()).hexdigest(), 16) % 60 % every_minutes
        self.assertEqual([minute(n, 15) for n in ("host-a", "host-b", "host-c", "host-d")], [10, 10, 9, 4])
        for host in ("host-a", "host-c", "host-d", "主機 一", "x"):
            for every in (15, 10, 60, 1, 7 * 60):
                got = s.parse([{"name": "n", "when": {"every": "%dm" % every, "offset": "machine"}, "do": {"run": "r"}}], host)["items"][0]
                self.assertEqual((got["offset"], got["offset_by"], got["offset_note"]), (minute(host, every) * 60, "machine", None), (host, every))
        self.assertEqual([s.machine_offset(900, n) // 60 for n in ("host-a", "host-c", "host-d")], [10, 9, 4])
        plain = item({"every": "15m", "offset": "7m"})
        self.assertEqual((plain["offset"], plain["offset_by"], plain["offset_note"]), (420, None, None))

    def test_the_stable_name_is_the_one_in_the_machine_list_not_the_host_name(self):
        machines = {"machines": {"host-c": {"hostname": "wanders-with-the-network"}, "host-d": {"hostname": "kept"}}}
        spread = [{"name": "n", "when": {"every": "15m", "offset": "machine"}, "do": {"run": "r"}}]
        with mock.patch.object(s.config, "inventory", return_value=machines), \
                mock.patch.object(s.config, "stated_name", return_value=None):
            with mock.patch.object(s.config, "hostname", return_value="wanders-with-the-network"):
                self.assertEqual(s.stable_name(), ("host-c", None))
                self.assertEqual(s.machine_spread(900), (9 * 60, None))
                self.assertEqual(s.parse(spread)["items"][0]["offset"], 9 * 60)
            with mock.patch.object(s.config, "stated_name", return_value="kept"), \
                    mock.patch.object(s.config, "hostname", return_value="anything-today"):
                self.assertEqual((s.stable_name(), s.hostname(), s.machine_offset(900)), (("host-d", None), "host-d", 4 * 60))
            with mock.patch.object(s.config, "hostname", return_value="a-stranger"):
                name, why = s.stable_name()
                self.assertEqual((name, s.machine_spread(900)), (None, (0, why)))
                self.assertIn("不在機器清單上", why)
                got = s.parse(spread)["items"][0]
                self.assertEqual((got["offset"], got["offset_note"]), (0, why))
                self.assertIn("不在機器清單上，用 0", s.describe(got))
        with mock.patch.object(s.config, "inventory", return_value={}):
            self.assertEqual(s.machine_spread(900), (0, "沒有機器清單"))
        with mock.patch.object(s.config, "inventory", side_effect=s.config.ConfigError("壞了")):
            seconds, why = s.machine_spread(900)
            self.assertEqual(seconds, 0)
            self.assertIn("讀不出來", why)
            self.assertEqual(s.parse(spread)["items"][0]["offset_note"], why)
            self.assertEqual(s.machine_spread(900, "host-c"), (9 * 60, None))   # a name handed in is used as it is

    def test_log_and_channel_names_are_kept_as_written(self):
        got = s.parse([{"name": "a", "when": {"at": "08:00"}, "do": {"message": "m", "channel": "secretary", "log": "~/.cache/我的 紀錄.out"}},
                       {"name": "b", "when": {"every": "5m"}, "do": {"run": "{python} \"{plugin}/x.py\"", "log": "~/.cache/x.out"}}])["items"]
        self.assertEqual(got[0]["do"], {"message": "m", "channel": "secretary", "log": "~/.cache/我的 紀錄.out", "retry": False})
        traced = s.parse([{"name": "a", "when": {"at": "08:00"}, "do": {"message": "m", "trace": {"file": "~/x", "key": "k"}}},
                          {"name": "b", "when": {"at": "08:00"}, "do": {"run": "x", "trace": 5}}])
        self.assertEqual([i["do"]["trace"] for i in traced["items"]], [{"file": "~/x", "key": "k"}, 5],
                         "kept as written; one written wrong never stops the item (--compare says so)")
        self.assertEqual((got[1]["do"]["log"], got[1]["do"]["run"]), ("~/.cache/x.out", '{python} "{plugin}/x.py"'))

    SAMPLES = {  # the schedules of the plugin.json files as first written (3a.2)
        "chatlog": [{"name": "sync", "when": {"every": "15m", "offset": "machine"},
                     "do": {"run": "{python} \"{plugin}/chatlog.py\" sync", "output": "none"}}],
        "planner": [{"name": "morning", "when": {"at": "08:00", "days": "daily"}, "timezone": "machine",
                     "do": {"message": "⏰ 早報", "channel": "secretary", "log": "~/.cache/planner.out"}},
                    {"name": "weekly", "when": {"at": "21:00", "days": ["sun"]}, "timezone": "machine",
                     "do": {"message": "📅 每週回顧", "channel": "secretary", "log": "~/.cache/planner.out"}},
                    {"name": "remind", "when": {"every": "5m"},
                     "do": {"run": "/usr/bin/python3 \"{plugin}/remind.py\" {setting:planner.schedule_files}",
                            "output": "none", "log": "~/.cache/planner.out"}}],
        "progress": [{"name": "run", "when": {"every": "10m"},
                "do": {"run": "{python} \"{plugin}/progress.py\" run", "env": {"PROGRESS_CLAUDE": "{claude}"}, "output": "none",
                       "log": "~/.cache/progress/cron.out"}}],
        "usage": [{"name": "collect", "when": {"every": "1h", "offset": "7m"},
                   "do": {"run": "/usr/bin/python3 \"{plugin}/collect.py\"", "output": "none", "log": "~/.cache/usage.out"}}],
    }

    def test_schedules_of_the_plugin_files(self):
        got = {name: s.parse(items, "host-a")["items"] for name, items in self.SAMPLES.items()}
        self.assertEqual([(i["kind"], i["every"], i["offset_by"], i["scheduler"]) for i in got["chatlog"]],
                         [("every", 900, "machine", True)])  # only exports since 3a.10: may go into the scheduler
        sync = [{"name": "s", "when": {"every": "15m"}, "do": {"run": "~/agent-home/bin/home-pull --quiet"}}]
        if hasattr(s.plugin, "forbidden_run"):   # the real rule: the sync is refused without the mark
            self.assertRaises(s.ScheduleError, s.parse, sync, "host-a")
            s.parse([dict(sync[0], scheduler=False)], "host-a")
        self.assertEqual([(i["name"], i["kind"], i["timezone"], i["do"]["channel"] if "message" in i["do"] else None)
                          for i in got["planner"]],
                         [("morning", "at", "machine", "secretary"), ("weekly", "at", "machine", "secretary"),
                          ("remind", "every", None, None)])
        self.assertEqual((got["progress"][0]["every"], got["progress"][0]["do"]["env"], got["progress"][0]["do"]["log"]),
                         (600, {"PROGRESS_CLAUDE": "{claude}"}, "~/.cache/progress/cron.out"))
        self.assertEqual((got["usage"][0]["every"], got["usage"][0]["offset"]), (3600, 420))

    def test_schedules_of_the_plugin_files_in_this_engine(self):
        engine = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        seen = 0
        for folder in sorted(os.listdir(engine)) + [os.path.join("plugins", n) for n in
                                                     (sorted(os.listdir(os.path.join(engine, "plugins")))
                                                      if os.path.isdir(os.path.join(engine, "plugins")) else [])]:
            path = os.path.join(engine, folder, "plugin.json")
            if not os.path.isfile(path):
                continue
            with open(path, encoding="utf-8") as f:
                schedules = json.load(f).get("schedules")
            if schedules is None:
                continue
            seen += 1
            try:
                items = s.parse(schedules, "host-a")["items"]
            except s.ScheduleError as e:
                self.fail("%s: %s" % (path, e))
            for it in items:
                s.describe(it)
                s.latest_slot(it, utc("2026-10-06 00:00"), TAIPEI)
        if not seen:
            self.skipTest("no plugin.json with schedules in this engine yet")

    def test_check_lists_items_and_fails_on_a_bad_file(self):
        folder = tempfile.mkdtemp()
        good = write(os.path.join(folder, "我的 排程", "schedule one.json"), {"channel": "project-a", "items": [
            {"name": "lesson", "at": "00:30", "days": "daily", "text": "上課"},
            {"name": "poll", "enabled": False, "when": {"every": "1h", "offset": "7m"}, "do": {"run": "x"}},
            {"name": "spread", "when": {"every": "15m", "offset": "machine"}, "do": {"run": "x"}, "timezone": "machine"},
            {"name": "soon", "when": {"once": "2026-10-08 09:00"}, "do": {"message": "m"}, "late": "30m"}]})
        bad = write(os.path.join(folder, "壞 的.json"), "{not json")
        wrong = write(os.path.join(folder, "wrong.json"), {"items": [{"name": "a", "when": {"at": "25:00"}, "do": {"message": "m"}}]})
        out = io.StringIO()
        with mock.patch.object(s, "hostname", return_value="host-a"):
            self.assertEqual(s.check([good], out), 0)
        minutes = s.machine_offset(900, "host-a") // 60
        self.assertEqual(minutes, 10)
        self.assertEqual(out.getvalue().splitlines(), [
            good + " · lesson：開 00:30 daily（message，late 3h）",
            good + " · poll：關 every 1h offset 7m（run，late 1h）",
            good + " · spread：開 every 15m%s（依機器）（run，late 15m，機器時區）" % (" offset %dm" % minutes if minutes else ""),
            good + " · soon：開 once 2026-10-08 09:00（message，late 30m）"])
        out = io.StringIO()
        self.assertEqual(s.check([good, bad, wrong, os.path.join(folder, "missing.json")], out), 1)
        text = out.getvalue()
        self.assertIn(bad + "：讀不懂（不是正確的 JSON", text)
        self.assertIn(wrong + "：讀不懂（「a」：at 要是", text)
        self.assertIn("missing.json：讀不懂（讀不到", text)
        with mock.patch("sys.stdout", io.StringIO()), mock.patch("sys.stderr", io.StringIO()):
            self.assertEqual(s.main(["--check", good]), 0)
            self.assertEqual(s.main(["--check", bad]), 1)
            self.assertEqual(s.main([]), 2)


class Due(unittest.TestCase):
    def test_no_note_is_never_due(self):
        """What nobody saw being missed is not made up for (the manager's rule)."""
        for when, now in (({"at": "08:00"}, "2026-10-06 08:00"), ({"at": "08:00"}, "2026-10-06 10:00"),
                          ({"every": "10m"}, "2026-10-06 08:00"), ({"once": "2026-10-06 07:30"}, "2026-10-06 08:00")):
            self.assertIsNone(s.due(item(when), None, at(TAIPEI, now), TAIPEI), when)

    def test_at_once_a_day_from_its_minute_until_late(self):
        it = item({"at": "08:00"})
        day, before = at(TAIPEI, "2026-10-06 08:00"), at(TAIPEI, "2026-10-05 08:00")
        self.assertEqual(s.due(it, before, day - 60, TAIPEI), None)
        self.assertEqual(s.due(it, before, day, TAIPEI), day)
        self.assertEqual(s.due(it, before, day + 59, TAIPEI), day)
        self.assertEqual(s.due(it, day, day + 60, TAIPEI), None)                       # served
        self.assertEqual(s.due(it, before, day + 3 * 3600, TAIPEI), day)               # woke up just in time
        self.assertEqual(s.due(it, before, day + 3 * 3600 + 1, TAIPEI), None)          # too late: not at all
        self.assertEqual(s.due(item({"at": "08:00"}, enabled=False), before, day, TAIPEI), None)

    def test_first_seen_after_its_time_waits_for_the_next_one(self):
        it = item({"at": "08:00"})
        seen = at(TAIPEI, "2026-10-06 09:30")   # the moment it was first seen is what is noted
        self.assertEqual(s.due(it, seen, seen + 60, TAIPEI), None)
        self.assertEqual(s.due(it, seen, at(TAIPEI, "2026-10-07 07:59"), TAIPEI), None)
        self.assertEqual(s.due(it, seen, at(TAIPEI, "2026-10-07 08:00"), TAIPEI), at(TAIPEI, "2026-10-07 08:00"))
        every = item({"every": "10m", "offset": "7m"})
        self.assertEqual(s.due(every, seen, seen + 60, TAIPEI), None)                   # not at once either
        self.assertEqual(s.due(every, seen, at(TAIPEI, "2026-10-06 09:37"), TAIPEI), at(TAIPEI, "2026-10-06 09:37"))

    def test_at_on_some_days(self):
        it = item({"at": "20:30", "days": ["sun"]}, late="2h")
        sunday = at(TAIPEI, "2026-10-11 20:30")
        self.assertEqual(s.due(it, 0, sunday + 600, TAIPEI), sunday)
        self.assertEqual(s.due(it, 0, at(TAIPEI, "2026-10-12 20:30"), TAIPEI), None)   # Monday
        self.assertEqual(s.latest_slot(it, at(TAIPEI, "2026-10-14 12:00"), TAIPEI), sunday)

    def test_at_just_before_midnight_seen_late_the_same_night(self):
        it = item({"at": "23:30"})
        slot = at(TAIPEI, "2026-10-06 23:30")
        self.assertEqual(s.due(it, slot - 86400, at(TAIPEI, "2026-10-07 01:00"), TAIPEI), slot)  # the old script missed this
        self.assertEqual(s.due(it, slot, at(TAIPEI, "2026-10-07 01:00"), TAIPEI), None)

    def test_every_counts_from_local_midnight(self):
        it = item({"every": "10m", "offset": "7m"})
        self.assertEqual(s.latest_slot(it, at(TAIPEI, "2026-10-06 09:16"), TAIPEI), at(TAIPEI, "2026-10-06 09:07"))
        self.assertEqual(s.latest_slot(it, at(TAIPEI, "2026-10-06 09:17"), TAIPEI), at(TAIPEI, "2026-10-06 09:17"))
        self.assertEqual(s.latest_slot(it, at(TAIPEI, "2026-10-06 00:03"), TAIPEI), at(TAIPEI, "2026-10-05 23:57"))
        odd = item({"every": "7h"})  # 00:00, 07:00, 14:00, 21:00, then midnight again
        self.assertEqual(s.latest_slot(odd, at(TAIPEI, "2026-10-06 23:59"), TAIPEI), at(TAIPEI, "2026-10-06 21:00"))
        self.assertEqual(s.latest_slot(odd, at(TAIPEI, "2026-10-07 00:00"), TAIPEI), at(TAIPEI, "2026-10-07 00:00"))
        daily = item({"every": "24h", "offset": "6h"})
        self.assertEqual(s.latest_slot(daily, at(TAIPEI, "2026-10-07 05:00"), TAIPEI), at(TAIPEI, "2026-10-06 06:00"))

    def test_every_after_sleep_only_the_latest_round(self):
        it = item({"every": "10m", "offset": "7m"})
        last = at(TAIPEI, "2026-10-06 09:07")
        woke = at(TAIPEI, "2026-10-06 13:21")
        self.assertEqual(s.due(it, last, woke, TAIPEI), at(TAIPEI, "2026-10-06 13:17"))
        self.assertEqual(s.due(it, at(TAIPEI, "2026-10-06 13:17"), woke + 300, TAIPEI), None)
        slow = item({"every": "1h"}, late="10m")
        self.assertEqual(s.due(slow, last, at(TAIPEI, "2026-10-06 13:11"), TAIPEI), None)

    def test_item_on_the_machine_time_zone(self):
        there = s.FixedZone(-5 * 3600, "machine")  # stands for the machine's own zone
        mine = item({"at": "08:00"}, timezone="machine")
        plain = item({"at": "08:00"})
        now = utc("2026-10-06 13:00")  # 08:00 at -5, 21:00 in Taipei
        with mock.patch.object(s, "SystemZone", return_value=there):
            self.assertEqual(s.due(mine, 0, now, TAIPEI), now)
            self.assertEqual(s.due(mine, now - 86400, now - 60, TAIPEI), None)
            self.assertEqual(s.due(plain, at(TAIPEI, "2026-10-06 08:00"), now, TAIPEI), None)
            self.assertEqual(s.due(plain, 0, at(TAIPEI, "2026-10-06 08:00"), TAIPEI), at(TAIPEI, "2026-10-06 08:00"))
            self.assertIs(s.zone_for(mine, TAIPEI), there)
        self.assertIs(s.zone_for(plain, TAIPEI), TAIPEI)

    def test_once_and_once_past(self):
        it = item({"once": "2026-10-08 09:00"})
        slot = at(TAIPEI, "2026-10-08 09:00")
        seen = at(TAIPEI, "2026-10-06 09:00")
        self.assertEqual(s.due(it, seen, slot - 1, TAIPEI), None)
        self.assertEqual(s.due(it, seen, slot + 3600, TAIPEI), slot)
        self.assertEqual(s.due(it, slot, slot + 3600, TAIPEI), None)
        self.assertEqual(s.due(it, seen, slot + 4 * 3600, TAIPEI), None)      # past: ignored
        self.assertEqual(s.due(it, seen, slot + 400 * 86400, TAIPEI), None)   # for good
        self.assertEqual(s.due(it, slot + 60, slot + 120, TAIPEI), None)      # first seen after its time: never
        self.assertEqual(s.due(it, seen, at(BERLIN, "2026-10-08 09:00"), BERLIN), at(BERLIN, "2026-10-08 09:00"))
        self.assertNotEqual(at(BERLIN, "2026-10-08 09:00"), slot)

    def run_minutes(self, it, tz, start, end, last):
        t, served = at(tz, start), []
        while t < at(tz, end):
            got = s.due(it, last, t, tz)
            if got is not None:
                served.append(got)
                last = got
            t += 60
        return served

    def test_summer_time_begins(self):
        it = item({"at": "02:30"})
        slot = utc("2026-03-29 01:00")  # 03:00 summer time, the first minute that exists
        before = at(BERLIN, "2026-03-28 02:30")
        self.assertEqual(s.due(it, before, slot - 60, BERLIN), None)
        self.assertEqual(s.due(it, before, slot, BERLIN), slot)
        self.assertEqual(s.due(it, slot, slot + 3600, BERLIN), None)
        self.assertEqual(s.due(it, slot, at(BERLIN, "2026-03-30 02:30"), BERLIN), at(BERLIN, "2026-03-30 02:30"))
        whole_day = self.run_minutes(it, BERLIN, "2026-03-29 00:00", "2026-03-30 00:00", before)
        self.assertEqual(whole_day, [slot])   # minute by minute through the day: once, at 03:00
        self.assertEqual(self.run_minutes(item({"at": "02:00"}), BERLIN, "2026-03-29 00:00", "2026-03-30 00:00", before), [slot])
        self.assertEqual(self.run_minutes(item({"at": "03:00"}), BERLIN, "2026-03-29 00:00", "2026-03-30 00:00", before), [slot])
        self.assertEqual(self.run_minutes(item({"once": "2026-03-29 02:15"}), BERLIN, "2026-03-29 00:00", "2026-03-30 00:00", before), [slot])
        served = self.run_minutes(item({"every": "30m"}), BERLIN, "2026-03-29 00:00", "2026-03-30 00:00",
                                  at(BERLIN, "2026-03-28 23:30"))
        self.assertEqual(len(served), 46)  # 23 real hours that day, a round every 30 real minutes
        self.assertEqual({b - a for a, b in zip(served, served[1:])}, {1800})

    def test_summer_time_ends(self):
        served = self.run_minutes(item({"at": "02:30"}), BERLIN, "2026-10-25 00:00", "2026-10-26 00:00",
                                  at(BERLIN, "2026-10-24 02:30"))
        self.assertEqual(served, [utc("2026-10-25 00:30")])  # 02:30 happens twice: served the first time only
        served = self.run_minutes(item({"every": "1h", "offset": "7m"}), BERLIN, "2026-10-25 00:00", "2026-10-26 00:00",
                                  at(BERLIN, "2026-10-24 23:07"))
        self.assertEqual(len(served), 25)
        self.assertEqual({b - a for a, b in zip(served, served[1:])}, {3600})

    def test_a_week_minute_by_minute_serves_each_time_once(self):
        items = s.parse({"items": [
            {"name": "a", "when": {"at": "08:00"}, "do": {"message": "m"}},
            {"name": "b", "when": {"at": "20:30", "days": ["sat", "sun"]}, "do": {"message": "m"}},
            {"name": "c", "when": {"every": "6h", "offset": "1h"}, "do": {"run": "r"}},
            {"name": "d", "when": {"once": "2026-10-08 09:00"}, "do": {"message": "m"}}]})["items"]
        t, end, count = at(TAIPEI, "2026-10-05 00:00"), at(TAIPEI, "2026-10-12 00:00"), {}
        last = {it["name"]: t for it in items}  # all first seen as the week begins
        while t < end:
            for it in items:
                got = s.due(it, last[it["name"]], t, TAIPEI)
                if got is not None:
                    self.assertEqual(got, t)  # never asleep: served in its own minute
                    last[it["name"]] = got
                    count[it["name"]] = count.get(it["name"], 0) + 1
            t += 60
        self.assertEqual(count, {"a": 7, "b": 2, "c": 28, "d": 1})


class Plan(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.mkdtemp()
        self.one = write(os.path.join(self.folder, "插件 一", "schedule.json"), {"items": [
            {"name": "collect", "when": {"every": "1h"}, "do": {"run": "r"}, "late": "6h"},
            {"name": "evening", "when": {"at": "08:00"}, "do": {"message": "m"}, "late": "6h"},
            {"name": "morning", "when": {"at": "07:00"}, "do": {"message": "m"}, "late": "6h"}]})
        self.two = write(os.path.join(self.folder, "plugin two", "schedule.json"), {"channel": "project-a", "items": [
            {"name": "lesson", "at": "08:00", "days": "daily", "text": "t"},
            {"name": "off", "enabled": False, "at": "08:00", "text": "t"}]})
        self.woke = at(TAIPEI, "2026-10-06 08:00")
        self.keys = {n: s.sent_key(self.one if n != "lesson" and n != "off" else self.two, n)
                     for n in ("collect", "evening", "morning", "lesson", "off")}

    def names(self, found):
        return [d["item"]["name"] for d in found]

    def test_nothing_noted_nothing_due_everything_fresh(self):
        found, problems, fresh = s.plan([self.one, self.two], {}, self.woke, TAIPEI)
        self.assertEqual((found, problems), ([], []))
        self.assertEqual(sorted((d["item"]["name"], d["state"]) for d in fresh),
                         [("collect", "seen"), ("evening", "seen"), ("lesson", "seen"), ("morning", "seen"), ("off", "off")])

    def test_after_sleep_in_the_order_of_their_times(self):
        asleep = {k: done(self.woke - 20 * 3600) for k in self.keys.values()}   # all noted the day before
        found, problems, fresh = s.plan([self.one, self.two], asleep, self.woke, TAIPEI)
        self.assertEqual(problems, [])
        self.assertEqual([(os.path.basename(os.path.dirname(d["path"])), d["item"]["name"]) for d in found],
                         [("插件 一", "morning"), ("插件 一", "evening"), ("plugin two", "lesson"), ("插件 一", "collect")])
        self.assertEqual([d["slot"] for d in found], [self.woke - 3600, self.woke, self.woke, self.woke])
        self.assertEqual(found[0]["key"], os.path.realpath(self.one) + "#morning")
        self.assertEqual([d["item"]["name"] for d in fresh], ["off"])     # switched off: noted as off

    def test_only_items_with_a_note_are_made_up_for(self):
        some = {self.keys["evening"]: done(self.woke - 86400)}
        found, _, fresh = s.plan([self.one], some, self.woke + 3600, TAIPEI)
        self.assertEqual(self.names(found), ["evening"])
        self.assertEqual(sorted(self.names(fresh)), ["collect", "morning"])

    def test_served_items_are_left_out_and_a_bad_file_does_not_stop_the_others(self):
        bad = write(os.path.join(self.folder, "bad.json"), "3")
        sent = {self.keys["morning"]: done(self.woke - 3600), self.keys["collect"]: done(self.woke),
                self.keys["evening"]: done(self.woke - 86400)}
        found, problems, fresh = s.plan([bad, self.one, os.path.join(self.folder, "none.json")], sent, self.woke, TAIPEI)
        self.assertEqual((self.names(found), fresh), (["evening"], []))
        self.assertEqual([p for p, _ in problems], [bad, os.path.join(self.folder, "none.json")])

    def test_switched_on_again_or_rescheduled_starts_from_now(self):
        k = self.keys["evening"]
        day_before = self.woke - 86400
        off = {k: {"slot": day_before, "state": "off", "when": "at 480 daily"}}
        found, _, fresh = s.plan([self.one], off, self.woke + 60, TAIPEI)
        self.assertEqual((self.names(found), [(d["item"]["name"], d["state"]) for d in fresh if d["key"] == k]),
                         ([], [("evening", "seen")]))                    # was off at 08:00: not made up for
        moved = {k: {"slot": day_before, "state": "done", "when": "at 420 daily"}}   # its time was another then
        found, _, fresh = s.plan([self.one], moved, self.woke + 60, TAIPEI)
        self.assertEqual((self.names(found), [d["key"] for d in fresh if d["key"] == k]), ([], [k]))
        same = {k: {"slot": day_before, "state": "done", "when": "at 480 daily"}}
        self.assertEqual(self.names(s.plan([self.one], same, self.woke + 60, TAIPEI)[0]), ["evening"])
        already_off = {s.sent_key(self.two, "off"): {"slot": day_before, "state": "off"},
                       s.sent_key(self.two, "lesson"): done(self.woke)}
        self.assertEqual(s.plan([self.two], already_off, self.woke, TAIPEI)[2], [])  # nothing to note again

    def test_failed_runs_are_tried_again_only_when_the_item_says_so(self):
        path = write(os.path.join(self.folder, "r.json"), [
            {"name": "again", "when": {"at": "08:00"}, "do": {"run": "r", "retry": True}},
            {"name": "never", "when": {"at": "08:00"}, "do": {"run": "r"}}])
        failed = lambda attempts: {s.sent_key(path, n): {"slot": self.woke, "state": "failed", "attempts": attempts}
                                   for n in ("again", "never")}
        found, _, _ = s.plan([path], failed(1), self.woke + 120, TAIPEI)
        self.assertEqual([(d["item"]["name"], d["slot"], d["retry"]) for d in found], [("again", self.woke, True)])
        self.assertEqual(s.plan([path], failed(s.RETRY_MAX), self.woke + 120, TAIPEI)[0], [])
        self.assertEqual(s.plan([path], failed(1), self.woke + 4 * 3600, TAIPEI)[0], [])       # past its late
        tomorrow = s.plan([path], failed(s.RETRY_MAX), self.woke + 86400, TAIPEI)[0]
        self.assertEqual([(d["item"]["name"], d["retry"]) for d in tomorrow], [("again", False), ("never", False)])

    def test_without_a_zone_only_items_that_mean_a_time_of_day_wait(self):
        path = write(os.path.join(self.folder, "z.json"), [
            {"name": "clock", "when": {"at": "08:00"}, "do": {"message": "m"}},
            {"name": "date", "when": {"once": "2026-10-06 08:00"}, "do": {"message": "m"}},
            {"name": "count", "when": {"every": "10m"}, "do": {"run": "r"}},
            {"name": "local", "when": {"at": "08:00"}, "timezone": "machine", "do": {"message": "m"}}])
        seen = {s.sent_key(path, n): done(self.woke - 3600) for n in ("clock", "date", "count", "local")}
        with mock.patch.object(s, "SystemZone", return_value=TAIPEI):
            found, problems, fresh = s.plan([path], seen, self.woke, None)
            self.assertEqual((self.names(found), fresh), (["local", "count"], []))
            self.assertEqual([p[0] for p in problems], ["core.timezone"])
            self.assertIn("clock、date", problems[0][1])
            self.assertEqual(self.names(s.plan([path], seen, self.woke, TAIPEI)[0]), ["clock", "date", "local", "count"])
            self.assertEqual([s.needs_zone(i) for i in s.load(path)["items"]], [True, True, False, False])
            self.assertIsNone(s.due(s.load(path)["items"][0], 0, self.woke, None))

    def test_an_item_taken_out_of_its_file_is_gone_and_new_when_it_comes_back(self):
        state = {s.sent_key(self.one, n): {"slot": self.woke - 86400, "state": "done"} for n in ("evening", "taken out")}
        state[s.sent_key(self.two, "elsewhere")] = {"slot": 5, "state": "done"}
        other = "/another/schedule.json#x"
        state[other] = {"slot": 5, "state": "done"}
        j = s._judge([self.one, os.path.join(self.folder, "unreadable.json")], state, self.woke, TAIPEI, None)
        self.assertEqual([d["key"] for d in j["gone"]], [s.sent_key(self.one, "taken out")])   # only of files read well

    def test_a_once_first_seen_just_after_its_time_is_served_an_older_one_is_said_once(self):
        path = write(os.path.join(self.folder, "o.json"), [
            {"name": "just", "when": {"once": "2026-10-06 07:59"}, "do": {"message": "m"}},
            {"name": "edge", "when": {"once": "2026-10-06 07:58"}, "do": {"message": "m"}},
            {"name": "old", "when": {"once": "2026-10-06 07:57"}, "do": {"message": "m"}},
            {"name": "ahead", "when": {"once": "2026-10-06 09:00"}, "do": {"message": "m"}},
            {"name": "clock", "when": {"at": "07:59"}, "do": {"message": "m"}},
            {"name": "count", "when": {"every": "1m"}, "do": {"run": "r"}}])
        found, problems, fresh = s.plan([path], {}, self.woke, TAIPEI)
        self.assertEqual(s.ONCE_GRACE, 120)
        self.assertEqual([(d["item"]["name"], d["slot"]) for d in found], [("edge", self.woke - 120), ("just", self.woke - 60)])
        self.assertEqual({d["item"]["name"]: d.get("start") for d in fresh},
                         {"just": self.woke - 61, "edge": self.woke - 121, "old": None, "ahead": None, "clock": None, "count": None})
        self.assertEqual(len(problems), 1)
        self.assertIn("「old」這一筆 once（2026-10-06 07:57）第一次見到時已經過期，不會發", problems[0][1])

    def test_a_note_more_than_a_day_ahead_starts_over_and_says_so(self):
        k = self.keys["evening"]
        day = {k: {"slot": self.woke + 86400, "state": "done", "when": "at 480 daily"}}
        found, problems, fresh = s.plan([self.one], day, self.woke, TAIPEI)
        self.assertEqual([d["key"] for d in fresh if d["key"] == k], [])      # a day or less: left alone, nothing due
        more = {k: {"slot": self.woke + 86400 + 1, "state": "done", "when": "at 480 daily"}}
        found, problems, fresh = s.plan([self.one], more, self.woke, TAIPEI)
        self.assertEqual(([d["key"] for d in fresh if d["key"] == k], self.names(found)), ([k], []))
        self.assertEqual(len([p for p in problems if "evening" in p[1] and "晚 24h 以上" in p[1]]), 1)

    def test_for_an_every_item_too_far_ahead_is_twice_its_interval(self):
        k = self.keys["collect"]                                           # every 1h
        for ahead, reset in ((2 * 3600, False), (2 * 3600 + 1, True), (23 * 3600, True)):
            state = {k: {"slot": self.woke + ahead, "state": "done", "when": "every 3600 0"}}
            found, problems, fresh = s.plan([self.one], state, self.woke, TAIPEI)
            self.assertEqual(([d["key"] for d in fresh if d["key"] == k] == [k],
                              len([p for p in problems if "collect" in p[1] and "晚 2h 以上" in p[1]])), (reset, int(reset)), ahead)
        k = self.keys["evening"]                                           # at 08:00: 23 hours ahead is left alone
        state = {k: {"slot": self.woke + 23 * 3600, "state": "done", "when": "at 480 daily"}}
        self.assertEqual([d["key"] for d in s.plan([self.one], state, self.woke, TAIPEI)[2] if d["key"] == k], [])
        self.assertEqual((s.FUTURE_ROUNDS, s.FUTURE_MAX), (2, 86400))

    def test_key_follows_the_real_path(self):
        link = os.path.join(self.folder, "link")
        os.symlink(os.path.dirname(self.one), link)
        self.assertEqual(s.sent_key(os.path.join(link, "schedule.json"), "n"), s.sent_key(self.one, "n"))


class Home(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp()
        patch = mock.patch.dict(os.environ, {"HOME": self.home, "ALT_RUNTIME": os.path.join(self.home, "beside")})
        patch.start()
        self.addCleanup(patch.stop)

    def raw(self, machine="host-a", dry=False):
        with open(s.sent_path(machine, dry), encoding="utf-8") as f:
            return f.read()


class State(Home):
    def test_paths_are_worked_out_from_the_home_of_the_moment(self):
        self.assertEqual(s.sent_path("host-a"), os.path.join(self.home, ".local/state/altention/scheduler/sent.host-a.json"))
        self.assertEqual(s.sent_path("host-a", dry=True), os.path.join(self.home, ".local/state/altention/scheduler/dry.host-a.json"))
        self.assertEqual(os.path.dirname(s.log_path("我的 機器/x")), s.state_dir())
        self.assertEqual(os.path.basename(s.log_path("我的 機器/x")), "would-run.我的 機器_x.jsonl")
        self.assertEqual(os.path.basename(s.log_path("host-a", "problems")), "problems.host-a.jsonl")

    def test_marks_per_machine_merge_and_survive(self):
        k1, k2 = "/資料 夾/schedule.json#早 報", "/b/schedule.json#x"
        t = at(TAIPEI, "2026-10-06 08:00")
        self.assertEqual(s.read_sent("host-a"), {})
        s.mark_sent("host-a", {k1: t}, TAIPEI)
        s.mark_sent("host-a", {k2: t + 60}, TAIPEI)
        s.mark_sent("host-a", {k1: t - 86400}, TAIPEI)        # an older time never replaces a newer one
        s.mark_sent("host-b", {k1: t + 7200}, TAIPEI)
        s.mark_sent("host-a", {k1: t + 30}, TAIPEI, dry=True)
        self.assertEqual(s.read_sent("host-a"), {k1: t, k2: t + 60})
        self.assertEqual(s.read_sent("host-b"), {k1: t + 7200})
        self.assertEqual(s.read_sent("host-a", dry=True), {k1: t + 30})
        data = json.loads(self.raw())
        self.assertEqual((data["format"], data["machine"]), (2, "host-a"))
        self.assertEqual((data["sent"][k1]["text"], data["sent"][k1]["state"]), ("2026-10-06T08:00:00+08:00", "done"))
        self.assertEqual(sorted(n for n in os.listdir(s.state_dir()) if n.endswith(".tmp")), [])

    def test_a_time_as_text_is_not_a_note(self):  # format 1 never existed anywhere: not read (2026-10-07)
        write(s.sent_path("host-a"), {"format": 1, "machine": "host-a", "sent": {"k": "2026-10-06T08:00:00+08:00"}})
        self.assertRaises(s.StateUnreadable, s.read_state, "host-a")

    def test_a_state_that_cannot_be_read_is_never_empty_and_never_replaced(self):
        s.mark_sent("host-a", {"k": 60}, TAIPEI)
        good = self.raw()
        path = s.sent_path("host-a")
        bodies = [good[:40], "", "[]\n", "3", '{"sent": []}', '{"sent": {"k": "soon"}}', '{"sent": {"k": 3}}',
                  '{"sent": {"k": {"slot": "x", "state": "done"}}}', '{"sent": {"k": {"slot": 5, "state": "odd"}}}',
                  '{"sent": {"k": {"slot": true, "state": "done"}}}', "\xff\xfe\x00binary"]
        for body in bodies:
            with open(path, "w", encoding="latin-1") as f:
                f.write(body)
            before = open(path, "rb").read()
            self.assertRaises(s.StateUnreadable, s.read_state, "host-a")
            self.assertRaises(s.StateUnreadable, s.read_sent, "host-a")
            self.assertRaises(s.StateUnreadable, s.mark_sent, "host-a", {"k": 120}, TAIPEI)
            self.assertRaises(s.StateUnreadable, s.mark_seen, "host-a", [], 0)
            self.assertRaises(s.StateUnreadable, s.claim, "host-a", "k", 120)
            self.assertFalse(s.finish("host-a", "k", 60, True))
            self.assertEqual(open(path, "rb").read(), before, body)

    def test_a_folder_or_an_unreadable_file_in_its_place_is_unreadable_too(self):
        os.makedirs(s.sent_path("host-a"))
        self.assertRaises(s.StateUnreadable, s.read_state, "host-a")
        self.assertRaises(s.StateUnreadable, s.claim, "host-a", "k", 120)
        self.assertTrue(os.path.isdir(s.sent_path("host-a")))
        s.mark_sent("host-b", {"k": 60}, TAIPEI)
        with mock.patch("builtins.open", side_effect=OSError(116, "Stale file handle")):
            self.assertRaises(s.StateUnreadable, s.read_state, "host-b")
        if os.getuid():
            os.chmod(s.sent_path("host-b"), 0)
            self.assertRaises(s.StateUnreadable, s.read_state, "host-b")
            os.chmod(s.sent_path("host-b"), 0o644)
        self.assertEqual(s.read_sent("host-b"), {"k": 60})

    def test_marks_made_at_the_same_moment_are_all_kept(self):
        threads = [threading.Thread(target=s.mark_sent, args=("host-a", {"k%d" % n: 1000 + n}, TAIPEI)) for n in range(24)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(s.read_sent("host-a"), {"k%d" % n: 1000 + n for n in range(24)})

    def test_the_file_in_place_is_whole_while_a_new_one_is_written(self):
        s.mark_sent("host-a", {"k": 60}, TAIPEI)
        with mock.patch.object(s.os, "replace", side_effect=OSError("disk")):
            self.assertRaises(OSError, s.mark_sent, "host-a", {"k": 120}, TAIPEI)
            self.assertRaises(s.StateUnwritable, s.claim, "host-a", "k", 120)   # not "someone has it"
            self.assertFalse(s.finish("host-a", "k", 60, True))
        self.assertEqual(s.read_sent("host-a"), {"k": 60})
        self.assertEqual([n for n in os.listdir(s.state_dir()) if n.endswith(".tmp")], [])

    def test_log_is_renamed_beyond_its_limit(self):
        s.record("host-a", [])
        self.assertFalse(os.path.exists(s.log_path("host-a")))
        for n in range(5):
            s.record("host-a", [{"n": n, "text": "排 程" * 10}], limit=150)
        path = s.log_path("host-a")
        with open(path, encoding="utf-8") as f:
            now = [json.loads(line)["n"] for line in f]
        with open(path + ".1", encoding="utf-8") as f:
            before = [json.loads(line)["n"] for line in f]
        self.assertEqual(sorted(os.listdir(s.state_dir())), ["would-run.host-a.jsonl", "would-run.host-a.jsonl.1"])
        self.assertEqual((before + now)[-len(now):], now)
        self.assertEqual(now[-1], 4)
        self.assertLessEqual(os.path.getsize(path), 150 + 120)
        self.assertGreater(s.LOG_MAX, 100000)

    def test_a_log_that_cannot_be_written_stops_nothing(self):
        os.makedirs(s.log_path("host-a"))
        s.record("host-a", [{"n": 1}])


class Claim(Home):
    def test_first_claim_wins_and_later_times_can_be_claimed(self):
        s.mark_seen("host-a", [{"key": "k", "state": "seen", "item": {"mark": "at 480 daily"}}], 1000, TAIPEI)
        self.assertTrue(s.claim("host-a", "k", 2000, now=2005, tz=TAIPEI))
        self.assertFalse(s.claim("host-a", "k", 2000, now=2006, tz=TAIPEI))
        self.assertFalse(s.claim("host-a", "k", 1500))                    # an earlier time: over
        entry = s.read_state("host-a")["k"]
        self.assertEqual((entry["slot"], entry["state"], entry["at"], entry["attempts"], entry["when"]),
                         (2000, "claimed", 2005, 1, "at 480 daily"))
        self.assertTrue(s.finish("host-a", "k", 2000, True, now=2010))
        self.assertFalse(s.finish("host-a", "k", 2000, True))            # said once
        self.assertEqual(s.read_state("host-a")["k"]["state"], "done")
        self.assertFalse(s.claim("host-a", "k", 2000))
        self.assertTrue(s.claim("host-a", "k", 3000, mark="at 540 daily"))
        self.assertEqual(s.read_state("host-a")["k"]["when"], "at 540 daily")
        self.assertTrue(s.claim("host-a", "never seen", 3000))           # the caller asked: claim does not judge first sight

    def test_a_failed_run_stays_on_record_and_is_retried_only_when_asked(self):
        self.assertTrue(s.claim("host-a", "k", 2000))
        self.assertTrue(s.finish("host-a", "k", 2000, False, "exit 3：壞 了", now=2030))
        entry = s.read_state("host-a")["k"]
        self.assertEqual((entry["state"], entry["note"], entry["at"]), ("failed", "exit 3：壞 了", 2030))
        self.assertFalse(s.claim("host-a", "k", 2000))                    # by default: not again
        for attempt in range(2, s.RETRY_MAX + 1):
            self.assertTrue(s.claim("host-a", "k", 2000, retry=True))
            self.assertFalse(s.claim("host-a", "k", 2000, retry=True))   # being run: not twice
            self.assertEqual(s.read_state("host-a")["k"]["attempts"], attempt)
            s.finish("host-a", "k", 2000, False, "again")
        self.assertFalse(s.claim("host-a", "k", 2000, retry=True))       # tried RETRY_MAX times
        self.assertTrue(s.claim("host-a", "k", 3000))

    def test_a_run_that_never_reported_is_not_served_again(self):
        self.assertTrue(s.claim("host-a", "k", 2000))                     # ...and the process died
        self.assertFalse(s.claim("host-a", "k", 2000, retry=True))
        self.assertEqual(s.read_state("host-a")["k"]["state"], "claimed")

    def test_stuck_lists_claims_whose_run_never_reported(self):
        folder = os.path.join(self.home, "插件 #一")
        sched = write(os.path.join(folder, "schedule.json"), [])
        k1, k2, k3 = s.sent_key(sched, "早 報"), s.sent_key(sched, "a#b"), "/gone/schedule.json#old"
        for key, slot, at_ in ((k1, 2000, 2005), (k2, 2100, 2500), (k3, 1000, 1001)):
            self.assertTrue(s.claim("host-a", key, slot, now=at_, tz=TAIPEI))
        s.claim("host-a", "/x#done", 2000, now=2005)
        s.finish("host-a", "/x#done", 2000, True)
        s.claim("host-a", "/x#failed", 2000, now=2005)
        s.finish("host-a", "/x#failed", 2000, False, "exit 1")
        got = s.stuck("host-a", 600, now=3000, tz=TAIPEI)
        self.assertEqual([(d["key"], d["path"], d["item"], d["slot"], d["at"], d["attempts"]) for d in got],
                         [(k3, "/gone/schedule.json", "old", 1000, 1001, 1),
                          (k1, os.path.realpath(sched), "早 報", 2000, 2005, 1)])
        self.assertEqual((got[1]["text"], got[1]["at_text"]), (TAIPEI.iso(2000), TAIPEI.iso(2005)))
        s.claim("host-c", k1, at(TAIPEI, "2026-10-06 08:00"), now=at(TAIPEI, "2026-10-06 08:00") + 5)   # claimed without a zone
        self.assertTrue(s.read_state("host-c")[k1]["text"].endswith("+00:00"))
        with zone_setting("Asia/Taipei"):
            self.assertEqual(s.stuck("host-c", 600)[0]["text"], "2026-10-06T08:00:00+08:00")       # shown in the instance's zone
        with zone_setting("Nowhere/Never"), mock.patch.object(s, "SystemZone", return_value=BERLIN):
            self.assertEqual(s.stuck("host-c", 600)[0]["text"], "2026-10-06T02:00:00+02:00")       # zone unknown: the machine's
        self.assertEqual([d["item"] for d in s.stuck("host-a", 60, now=3000)], ["old", "早 報", "a#b"])
        self.assertEqual(s.stuck("host-a", 600, now=3000, dry=True), [])
        self.assertEqual(s.stuck("host-b", 0), [])
        with open(s.sent_path("host-a"), "w") as f:
            f.write("{")
        self.assertRaises(s.StateUnreadable, s.stuck, "host-a", 600)

    def test_of_many_claims_at_once_one_wins(self):
        code = ("import os, sys, time\nsys.path.insert(0, %r)\nfrom altention import scheduler as s\n"
                "while not os.path.exists(os.path.join(os.environ['HOME'], 'go')): time.sleep(0.001)\n"
                "print(int(s.claim('host-a', 'k', 2000)))\n" % os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
        for prepared in (True, False):   # with a state file, and with none yet
            for name in os.listdir(self.home):
                if name == "go":
                    os.unlink(os.path.join(self.home, name))
            if os.path.exists(s.sent_path("host-a")):
                os.unlink(s.sent_path("host-a"))
            if prepared:
                s.mark_sent("host-a", {"k": 1000}, TAIPEI)
            procs = [subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE) for _ in range(8)]
            time.sleep(0.4)
            open(os.path.join(self.home, "go"), "w").close()
            wins = sum(int(p.communicate()[0]) for p in procs)
            self.assertEqual(wins, 1, "prepared=%s" % prepared)
            self.assertEqual(s.read_state("host-a")["k"]["slot"], 2000)


class Round(Home):
    def setUp(self):
        super().setUp()
        self.file = write(os.path.join(self.home, "插件 一", "schedule.json"), [
            {"name": "morning", "when": {"at": "08:00"}, "do": {"message": "m"}},
            {"name": "collect", "when": {"every": "10m", "offset": "7m"}, "do": {"run": "x"}},
            {"name": "review", "when": {"once": "2026-10-06 07:30"}, "do": {"message": "r"}}])

    def round(self, text, **kw):
        return s.start_round([self.file], "host-a", now=at(TAIPEI, text), tz=kw.pop("tz", TAIPEI), **kw)

    def serve(self, r, text):
        out = []
        for d in r["due"]:
            if s.claim("host-a", d["key"], d["slot"], now=at(TAIPEI, text), tz=TAIPEI, mark=d["item"]["mark"], retry=d["retry"]):
                s.finish("host-a", d["key"], d["slot"], True)
                out.append("%s@%s" % (d["item"]["name"], TAIPEI.iso(d["slot"])[11:16]))
        return out

    def test_first_round_serves_nothing_and_notes_where_each_item_starts(self):
        self.assertFalse(os.path.exists(s.sent_path("host-a")))
        r = self.round("2026-10-06 09:30")
        self.assertEqual((r["due"], r["skipped"], r["notify"], r["held"], r["gone"], r["not_mine"]), ([], None, [], [], [], []))
        self.assertEqual(len(r["problems"]), 1)   # the once at 07:30, past when first seen: said this once
        self.assertIn("review", r["problems"][0][1])
        self.assertEqual(self.round("2026-10-06 09:30")["problems"], [])
        self.assertEqual(sorted(d["item"]["name"] for d in r["fresh"]), ["collect", "morning", "review"])
        now = at(TAIPEI, "2026-10-06 09:30")
        state = s.read_state("host-a")
        self.assertEqual({(e["slot"], e["state"]) for e in state.values()}, {(now, "seen")})
        self.assertEqual(self.round("2026-10-06 09:30")["due"] + self.round("2026-10-06 09:36")["due"], [])
        self.assertEqual(self.serve(self.round("2026-10-06 09:37"), "2026-10-06 09:37"), ["collect@09:37"])
        self.assertEqual(self.serve(self.round("2026-10-07 08:00"), "2026-10-07 08:00"), ["collect@07:57", "morning@08:00"])
        self.assertEqual(s.read_state("host-a")[s.sent_key(self.file, "review")]["state"], "seen")   # its time was before: never

    def test_an_item_added_later_starts_when_it_is_first_seen(self):
        self.round("2026-10-06 09:30")
        write(self.file, [{"name": "morning", "when": {"at": "08:00"}, "do": {"message": "m"}},
                          {"name": "new", "when": {"at": "07:00"}, "do": {"message": "n"}}])
        r = self.round("2026-10-06 09:31")
        self.assertEqual((r["due"], [d["item"]["name"] for d in r["fresh"]]), ([], ["new"]))
        self.assertEqual(self.serve(self.round("2026-10-07 07:00"), "2026-10-07 07:00"), ["new@07:00"])

    def test_clock_far_ahead_only_what_was_seen_is_made_up_for(self):
        self.round("2026-10-06 07:59")
        self.assertEqual(self.serve(self.round("2026-10-06 08:00"), "2026-10-06 08:00"), ["morning@08:00"])
        write(self.file, [{"name": "morning", "when": {"at": "08:00"}, "do": {"message": "m"}},
                          {"name": "collect", "when": {"every": "10m", "offset": "7m"}, "do": {"run": "x"}},
                          {"name": "late-new", "when": {"at": "09:00"}, "do": {"message": "n"}},
                          {"name": "once-new", "when": {"once": "2026-10-07 08:30"}, "do": {"message": "o"}}])
        r = self.round("2026-10-07 10:00")   # 26 hours on
        self.assertEqual(self.serve(r, "2026-10-07 10:00"), ["morning@08:00", "collect@09:57"])
        self.assertEqual(sorted(d["item"]["name"] for d in r["fresh"]), ["late-new", "once-new"])
        self.assertEqual(self.serve(self.round("2026-10-07 12:30"), "2026-10-07 12:30"), ["collect@12:27"])
        back = self.round("2026-10-07 07:00")                               # the clock went back five hours
        self.assertEqual(self.serve(back, "2026-10-07 07:00"), [])
        self.assertEqual([d["item"]["name"] for d in back["fresh"]], ["collect"])   # an every item far ahead: starts over
        self.assertEqual(len([p for p in back["problems"] if "collect" in p[1]]), 1)
        self.assertEqual(self.serve(self.round("2026-10-07 07:07"), "2026-10-07 07:07"), ["collect@07:07"])   # not silent for hours
        self.assertEqual(self.serve(self.round("2026-10-07 12:31"), "2026-10-07 12:31"), ["collect@12:27"])
        self.assertEqual(self.serve(self.round("2026-10-07 12:32"), "2026-10-07 12:32"), [])
        self.assertEqual(self.serve(self.round("2026-10-08 09:00"), "2026-10-08 09:00"),
                         ["morning@08:00", "collect@08:57", "late-new@09:00"])

    def test_unreadable_state_four_rounds_nothing_then_set_aside_and_started_anew(self):
        self.round("2026-10-06 07:59")
        path = s.sent_path("host-a")
        good = self.raw()
        with open(path, "w") as f:
            f.write(good[:40])
        for n in range(1, 5):
            r = self.round("2026-10-06 08:%02d" % n)
            self.assertEqual((r["due"], r["fresh"], r["skipped"], r["notify"]), ([], [], "state", []))
            self.assertIn("第 %d 輪" % n, r["problems"][0][1])
            self.assertEqual(self.raw(), good[:40])                       # neither served again nor replaced
            self.assertRaises(s.StateUnreadable, s.claim, "host-a", "k", 5)
        r = self.round("2026-10-06 08:05")                                 # the fifth
        self.assertEqual(s.NOTIFY_AFTER, 5)
        self.assertEqual((r["due"], r["skipped"], len(r["notify"])), ([], None, 1))
        self.assertEqual(sorted(d["item"]["name"] for d in r["fresh"]), ["collect", "morning", "review"])
        kept = [n for n in os.listdir(s.state_dir()) if ".unreadable-" in n]
        self.assertEqual(kept, ["sent.host-a.json.unreadable-20261006T000500Z"])
        with open(os.path.join(s.state_dir(), kept[0])) as f:
            self.assertEqual(f.read(), good[:40])                         # the bad file is kept, as it was
        self.assertIn(kept[0], r["notify"][0])
        self.assertEqual({e["state"] for e in s.read_state("host-a").values()}, {"seen"})
        self.assertFalse(os.path.exists(s.unreadable_path("host-a")))      # the count starts over
        self.assertEqual(self.serve(self.round("2026-10-06 08:06"), "2026-10-06 08:06"), [])     # 08:00 is not made up for
        self.assertEqual(self.serve(self.round("2026-10-06 08:07"), "2026-10-06 08:07"), ["collect@08:07"])
        with open(s.log_path("host-a", "problems"), encoding="utf-8") as f:
            lines = [json.loads(line) for line in f]
        self.assertEqual(len([line for line in lines if line.get("skipped") == "state"]), 4)

    def test_a_state_value_from_a_newer_version_goes_the_same_way(self):
        self.round("2026-10-06 07:59")
        data = json.loads(self.raw())
        key = s.sent_key(self.file, "morning")
        data["sent"][key]["state"] = "postponed"                           # this version does not know it
        write(s.sent_path("host-a"), data)
        for n in range(1, 5):
            self.assertEqual(self.round("2026-10-06 08:%02d" % n)["skipped"], "state")
        r = self.round("2026-10-06 08:05")
        self.assertEqual((r["skipped"], len(r["notify"]), len(r["fresh"])), (None, 1, 3))

    def test_a_state_that_cannot_be_moved_keeps_the_round_stopped_and_tells_once_a_day(self):
        self.round("2026-10-06 07:59")
        with open(s.sent_path("host-a"), "w") as f:
            f.write("{")
        told = []
        start = at(TAIPEI, "2026-10-06 08:00")
        with mock.patch.object(s.os, "rename", side_effect=OSError(5, "Input/output error")):
            for n in range(1, 2 * 1440 + 10):
                r = s.start_round([self.file], "host-a", now=start + 60 * n, tz=TAIPEI)
                self.assertEqual((r["due"], r["skipped"]), ([], "state"))
                if r["notify"]:
                    told.append(n)
        self.assertEqual(told, [5, 5 + 1440, 5 + 2880])                    # the fifth round, then once a day
        self.assertEqual(self.raw(), "{")

    def test_set_aside_leaves_a_state_that_reads_again_alone(self):
        self.round("2026-10-06 07:59")
        before = self.raw()
        self.assertIsNone(s.set_aside("host-a"))
        self.assertEqual(self.raw(), before)
        os.unlink(s.sent_path("host-a"))
        os.makedirs(s.sent_path("host-a"))                                  # a folder in its place
        for n in range(1, 5):
            self.assertEqual(self.round("2026-10-06 08:%02d" % n)["skipped"], "state")
        r = self.round("2026-10-06 08:05")
        self.assertEqual((r["skipped"], os.path.isfile(s.sent_path("host-a"))), (None, True))
        self.assertTrue(os.path.isdir(s.sent_path("host-a") + ".unreadable-20261006T000500Z"))

    def test_starting_points_that_cannot_be_noted_stop_the_round(self):
        self.round("2026-10-06 07:59")
        write(self.file, [{"name": "morning", "when": {"at": "08:00"}, "do": {"message": "m"}},
                          {"name": "new", "when": {"at": "07:00"}, "do": {"message": "n"}}])
        with mock.patch.object(s.os, "replace", side_effect=OSError(5, "Input/output error")):
            r = self.round("2026-10-06 08:00")
        self.assertEqual((r["due"], r["skipped"]), ([], "state"))
        self.assertEqual(self.serve(self.round("2026-10-06 08:01"), "2026-10-06 08:01"), ["morning@08:00"])

    def no_zone(self, text, **kw):
        return s.start_round([self.file], "host-a", now=at(TAIPEI, text), **kw)

    def test_zone_unknown_only_items_that_mean_a_time_of_day_wait(self):
        self.round("2026-10-06 07:59")
        before = None
        for label, patch in (("settings broken", mock.patch.object(s.config, "settings", side_effect=s.config.ConfigError("壞了"))),
                             ("mistyped", zone_setting("Asia/Taipeii")), ("unknown", zone_setting("Nowhere/Never")),
                             ("core not an object", zone_setting(settings={"core": []}))):
            s.clear_bad_rounds("host-a", kind="zone")
            told = []
            with patch, mock.patch.object(s, "SystemZone", return_value=TAIPEI):
                for n in range(0, 8):
                    r = self.no_zone("2026-10-06 08:%02d" % n)
                    self.assertEqual((r["skipped"], r["zone"], [d["item"]["name"] for d in r["held"]]), (None, None, ["morning", "review"]), label)
                    self.assertEqual([p[0] for p in r["problems"]], ["core.timezone"], label)
                    self.assertIn("第 %d 輪" % (n + 1), r["problems"][0][1])
                    self.assertNotIn("morning", [d["item"]["name"] for d in r["due"]])
                    told.append(bool(r["notify"]))
                    if label == "settings broken" and n == 7:
                        self.assertEqual([d["item"]["name"] for d in r["due"]], ["collect"])   # counting goes on
                        self.serve(r, "2026-10-06 08:07")
            self.assertEqual(told, [False] * 4 + [True, False, False, False], label)
        with zone_setting("Asia/Taipei"):
            r = self.no_zone("2026-10-06 08:08")                           # the zone is back: the 08:00 it missed is made up for
        self.assertEqual(([d["item"]["name"] for d in r["due"]], r["held"], r["problems"]), (["morning"], [], []))
        self.assertFalse(os.path.exists(s.unreadable_path("host-a", kind="zone")))

    def test_zone_unknown_is_told_again_once_a_day(self):
        told = []
        start = at(TAIPEI, "2026-10-06 08:00")
        with zone_setting("Nowhere/Never"), mock.patch.object(s, "SystemZone", return_value=TAIPEI):
            for n in range(1, 1440 + 10):
                if s.start_round([self.file], "host-a", now=start + 60 * n)["notify"]:
                    told.append(n)
        self.assertEqual(told, [5, 5 + 1440])
        self.assertEqual(s.NOTIFY_AGAIN, 86400)

    def test_record_only_with_the_zone_unknown_still_counts(self):
        with zone_setting("Asia/Taipei"):
            s.record_only([self.file], "host-a", now=at(TAIPEI, "2026-10-06 07:59"))
        with zone_setting("Nowhere/Never"), mock.patch.object(s, "SystemZone", return_value=TAIPEI):
            found, problems = s.record_only([self.file], "host-a", now=at(TAIPEI, "2026-10-06 08:07"))
        self.assertEqual(([d["item"]["name"] for d in found], [p[0] for p in problems]), (["collect"], ["core.timezone"]))

    def test_without_the_setting_the_machine_zone_is_used(self):
        there = s.FixedZone(8 * 3600, "machine")
        with zone_setting(), mock.patch.object(s, "SystemZone", return_value=there):
            self.assertEqual(self.no_zone("2026-10-06 07:59")["skipped"], None)
            r = self.no_zone("2026-10-06 08:00")
        self.assertEqual(([d["item"]["name"] for d in r["due"]], r["problems"], r["zone"]), (["morning"], [], there))

    def test_a_slow_round_does_not_put_a_served_item_back_to_a_start(self):
        """A reads an empty state and stalls; C claims and finishes at 10:15; A wakes and notes its starts;
        the 10:16 round must not get 10:15 again."""
        write(self.file, [{"name": "quarter", "when": {"every": "15m"}, "do": {"run": "x"}}])
        key = s.sent_key(self.file, "quarter")
        t = lambda text: at(TAIPEI, text)
        a_state = s.read_state("host-a")                                    # A, at 10:14: nothing yet
        a_fresh = s.plan([self.file], a_state, t("2026-10-06 10:14"), TAIPEI)[2]
        self.assertEqual([d["key"] for d in a_fresh], [key])
        s.mark_sent("host-a", {key: t("2026-10-06 10:00")}, TAIPEI)         # meanwhile the item got a note...
        c = self.round("2026-10-06 10:15")                                  # ...and C serves 10:15
        self.assertEqual(self.serve(c, "2026-10-06 10:15"), ["quarter@10:15"])
        self.assertEqual(s.mark_seen("host-a", a_fresh, t("2026-10-06 10:14"), TAIPEI), 0)   # A wakes: not its to note any more
        entry = s.read_state("host-a")[key]
        self.assertEqual((entry["slot"], entry["state"]), (t("2026-10-06 10:15"), "done"))
        d = self.round("2026-10-06 10:16")
        self.assertEqual(d["due"], [])
        self.assertFalse(s.claim("host-a", key, t("2026-10-06 10:15")))

    def test_an_item_taken_out_loses_its_note_and_is_new_when_it_comes_back(self):
        full = [{"name": "morning", "when": {"at": "08:00"}, "do": {"message": "m"}},
                {"name": "noon", "when": {"at": "12:00"}, "do": {"message": "m"}}]
        write(self.file, full)
        other = write(os.path.join(self.home, "other.json"), [{"name": "x", "when": {"at": "08:00"}, "do": {"message": "m"}}])
        s.start_round([self.file, other], "host-a", now=at(TAIPEI, "2026-10-06 07:00"), tz=TAIPEI)
        write(self.file, full[1:])
        r = self.round("2026-10-06 07:30")                                  # other.json not handed in this time
        self.assertEqual([d["key"] for d in r["gone"]], [s.sent_key(self.file, "morning")])
        self.assertEqual(sorted(s.read_state("host-a")), sorted([s.sent_key(self.file, "noon"), s.sent_key(other, "x")]))
        write(self.file, full)
        r = self.round("2026-10-06 09:00")                                  # back, an hour after its time
        self.assertEqual((r["due"], [d["item"]["name"] for d in r["fresh"]]), ([], ["morning"]))
        write(self.file, "{ broken")
        r = self.round("2026-10-06 09:01")
        self.assertEqual((r["gone"], len(s.read_state("host-a"))), ([], 3))  # a file that cannot be read loses nothing

    def test_a_schedule_file_unreadable_five_rounds_is_told_with_its_plugin_and_path(self):
        good = self.file
        plug = write(os.path.join(self.home, "plugins", "我的 插件", "plugin.json"),
                     {"name": "notes", "schedules": [{"name": "tick", "when": {"every": "1m"}, "do": {"run": "x"}}]})
        plain = write(os.path.join(self.home, "某 處", "schedule.json"), [{"name": "tock", "when": {"every": "1m"}, "do": {"run": "x"}}])
        paths = [good, plug, plain]
        rnd = lambda n, **kw: s.start_round(paths, "host-a", now=at(TAIPEI, "2026-10-06 08:00") + 60 * n, tz=TAIPEI, **kw)
        r = rnd(0)
        self.assertEqual((r["notify"], r["problems"][1:]), ([], []))
        with open(plug, "w") as f:
            f.write('{"name": "notes", "schedules": [{"na')            # cut off
        with open(plain, "w") as f:
            f.write("{")
        told = {}
        for n in range(1, 9):
            r = rnd(n)
            self.assertEqual(r["skipped"], None)
            self.assertEqual([d["item"]["name"] for d in r["due"]], ["collect"] if n >= 7 else [])   # the good file goes on
            self.assertEqual(sorted(p[0] for p in r["problems"]), sorted([plug, plain]))
            if r["notify"]:
                told[n] = r["notify"]
        self.assertEqual(list(told), [5])                                  # at the fifth round, once
        self.assertEqual(len(told[5]), 2)
        about_plugin = [t for t in told[5] if plug in t][0]
        self.assertIn("插件「notes」的", about_plugin)                     # its name, remembered from when it read
        self.assertIn("連續 5 輪", about_plugin)
        about_plain = [t for t in told[5] if plain in t][0]
        self.assertNotIn("插件", about_plain)
        write(plain, [{"name": "tock", "when": {"every": "1m"}, "do": {"run": "x"}}])
        r = rnd(9)
        self.assertEqual(sorted(p[0] for p in r["problems"]), [plug])
        with open(plain, "w") as f:
            f.write("{")
        self.assertEqual([bool(rnd(n)["notify"]) for n in range(10, 15)], [False] * 4 + [True])   # its count started over
        later = [n for n in range(15, 15 + 1440) if rnd(n)["notify"]]
        self.assertEqual(later, [5 + 1440, 14 + 1440])                     # each once a day after its own first telling
        r = s.start_round(paths, "host-a", now=at(TAIPEI, "2026-10-09 08:00"), tz=TAIPEI, labels={plain: "given-name"})
        self.assertIn("插件「given-name」的", [t for t in r["notify"] if plain in t][0])

    def test_a_missing_schedule_file_counts_as_unreadable_and_the_count_is_per_machine(self):
        gone = os.path.join(self.home, "nowhere.json")
        for n in range(1, 5):
            self.assertEqual(s.start_round([gone], "host-a", now=1000 + 60 * n, tz=TAIPEI)["notify"], [])
        self.assertEqual(s.start_round([gone], "host-b", now=2000, tz=TAIPEI)["notify"], [])
        self.assertEqual(len(s.start_round([gone], "host-a", now=2000, tz=TAIPEI)["notify"]), 1)
        self.assertEqual(s.start_round([gone], "host-a", now=2060, tz=TAIPEI, dry=True)["notify"], [])   # counted apart
        read = lambda: open(s.files_path("host-a"), encoding="utf-8").read()
        before = read()
        s.start_round([self.file], "host-a", now=3000, tz=TAIPEI)          # no longer handed in: dropped
        self.assertNotIn("nowhere.json", read())
        self.assertNotEqual(read(), before)
        real_replace, moved = os.replace, []

        def watching(src, dst):
            moved.append(os.path.basename(dst))
            return real_replace(src, dst)
        with mock.patch.object(s.os, "replace", side_effect=watching):
            s.start_round([self.file], "host-a", now=3060, tz=TAIPEI)
        self.assertNotIn(os.path.basename(s.files_path("host-a")), moved)  # nothing changes: nothing written
        with open(s.files_path("host-a"), "w") as f:
            f.write("{")                                                    # its own file broken: starts over, no error
        self.assertEqual(s.start_round([gone], "host-a", now=4000, tz=TAIPEI)["notify"], [])

    def test_of_many_processes_in_the_fifth_round_one_is_told(self):
        bad = write(os.path.join(self.home, "壞 的.json"), "{")
        engine = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        code = ("import json, os, sys, time\nsys.path.insert(0, %r)\nfrom altention import scheduler as s\n"
                "while not os.path.exists(os.path.join(os.environ['HOME'], 'go')): time.sleep(0.001)\n"
                "tz = s.FixedZone(8 * 3600)\n"
                "r = s.start_round([%r], 'host-a', now=int(sys.argv[1]), tz=None if sys.argv[2] == 'zone' else tz)\n"
                "print(json.dumps(r['notify']))\n" % (engine, bad))
        start = at(TAIPEI, "2026-10-06 08:00")
        for what in ("file", "zone"):
            for name in ("files.host-a.json", "nozone.host-a.json"):
                if os.path.exists(os.path.join(s.state_dir(), name)):
                    os.unlink(os.path.join(s.state_dir(), name))
            told = []
            write(os.path.join(self.home, "altention", "instance", "plugins.json"),
                  {"core": {"timezone": "Nowhere/Never" if what == "zone" else "Asia/Taipei"}})
            for n in range(1, 8):
                go = os.path.join(self.home, "go")
                if os.path.exists(go):
                    os.unlink(go)
                procs = [subprocess.Popen([sys.executable, "-c", code, str(start + 60 * n), what], stdout=subprocess.PIPE)
                         for _ in range(8)]
                time.sleep(0.3)
                open(go, "w").close()
                outs = [json.loads(p.communicate()[0]) for p in procs]
                told.append(sum(len([t for t in o if (bad in t) == (what == "file")]) for o in outs))
            self.assertEqual(told, [0, 0, 0, 0, 1, 0, 0], what)             # the fifth round, one process
            counts = config_json(s.files_path("host-a"))[os.path.realpath(bad)]["count"] if what == "file" \
                else config_json(s.unreadable_path("host-a", kind="zone"))["count"]
            self.assertEqual(counts, 7, what)                               # eight processes a round count once

    def test_a_plugin_json_is_a_schedule_file(self):
        plug = write(os.path.join(self.home, "p", "plugin.json"), {
            "name": "notes", "title": "x", "schedules": [{"name": "tick", "when": {"at": "08:00"}, "do": {"message": "m"}}]})
        got = s.load(plug)
        self.assertEqual((got["plugin"], [i["name"] for i in got["items"]]), ("notes", ["tick"]))
        self.assertIsNone(s.load(self.file)["plugin"])
        s.start_round([plug], "host-a", now=at(TAIPEI, "2026-10-06 07:59"), tz=TAIPEI)
        r = s.start_round([plug], "host-a", now=at(TAIPEI, "2026-10-06 08:00"), tz=TAIPEI)
        self.assertEqual([d["key"] for d in r["due"]], [os.path.realpath(plug) + "#tick"])
        quiet = write(os.path.join(self.home, "q", "plugin.json"), {"name": "quiet", "title": "t"})
        self.assertEqual(s.load(quiet), {"channel": None, "items": [], "plugin": "quiet"})   # schedules nothing: empty, not broken
        for n in range(8):                                                  # so no false alarm after five rounds
            r = s.start_round([quiet], "host-b", now=1000 + 60 * n, tz=TAIPEI)
            self.assertEqual((r["problems"], r["notify"], r["due"]), ([], [], []))
        for wrong in ({"name": "p", "schedules": {"a": 1}}, {"name": "p", "schedules": None}, {"schedules": "x"}):
            self.assertRaises(s.ScheduleError, s.load, write(os.path.join(self.home, "w", "plugin.json"), wrong))
        self.assertRaises(s.ScheduleError, s.load, write(os.path.join(self.home, "w", "other.json"), {"title": "neither"}))
        self.assertRaises(s.ScheduleError, s.load, write(os.path.join(self.home, "w", "old.json"), {"name": "p", "items": "x"}))

    def test_every_plugin_file_of_this_engine_loads_as_a_schedule(self):
        engine = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        found = [os.path.join(engine, "altention", "core.plugin.json")]
        folders = sorted(os.listdir(engine))
        for sub in ("skills", "plugins"):
            if os.path.isdir(os.path.join(engine, sub)):
                folders += [os.path.join(sub, n) for n in sorted(os.listdir(os.path.join(engine, sub)))]
        found += [os.path.join(engine, f, "plugin.json") for f in folders if os.path.isfile(os.path.join(engine, f, "plugin.json"))]
        self.assertTrue(os.path.isfile(found[0]))
        loaded = {os.path.relpath(p, engine): s.load(p, "host-a") for p in found}     # none raises
        self.assertEqual(loaded[os.path.join("altention", "core.plugin.json")]["items"], [])   # it schedules nothing
        council = os.path.join("skills", "council", "plugin.json")
        if os.path.isdir(os.path.join(engine, "skills")):                  # the whole engine is here
            self.assertEqual(loaded[council]["items"], [])
        for rel, got in loaded.items():
            self.assertIsInstance(got["plugin"], str, rel)
        r = None
        for n in range(6):
            r = s.start_round(found, "host-c", now=1000 + 60 * n, tz=TAIPEI, host="host-a")
            self.assertEqual((r["problems"], r["notify"]), ([], []), n)

    def test_forget_drops_one_file_s_notes_and_only_when_asked(self):
        other = write(os.path.join(self.home, "另一 個", "schedule.json"), [{"name": "x", "when": {"at": "08:00"}, "do": {"message": "m"}}])
        alike = write(self.file + "2", [{"name": "morning", "when": {"at": "08:00"}, "do": {"message": "m"}}])
        s.start_round([self.file, other, alike], "host-a", now=at(TAIPEI, "2026-10-06 07:00"), tz=TAIPEI)
        s.start_round([self.file], "host-a", now=at(TAIPEI, "2026-10-06 07:00"), tz=TAIPEI, dry=True)
        self.round("2026-10-06 07:30")                                      # the others not handed in: kept
        self.assertEqual(len(s.read_state("host-a")), 5)
        link = os.path.join(self.home, "link.json")
        os.symlink(self.file, link)
        self.assertEqual(s.forget("host-a", link), 3)                       # by its real path
        self.assertEqual(sorted(s.read_state("host-a")), sorted([s.sent_key(other, "x"), s.sent_key(alike, "morning")]))
        self.assertEqual((s.forget("host-a", self.file), s.forget("host-b", other)), (0, 0))
        self.assertEqual(len(s.read_state("host-a", dry=True)), 3)          # the record-only notes are their own
        self.assertEqual(s.forget("host-a", self.file, dry=True), 3)
        r = self.round("2026-10-06 09:00")                                  # switched on again after its time
        self.assertEqual((r["due"], len(r["fresh"])), ([], 3))              # new: nothing made up for
        os.unlink(other)
        self.assertEqual(s.forget("host-a", other), 1)                      # the file itself may be gone already
        before = self.raw()
        with mock.patch.object(s.os, "replace", side_effect=OSError(28, "No space left on device")):
            self.assertRaises(s.StateUnwritable, s.forget, "host-a", self.file)
        self.assertEqual(self.raw(), before)
        with open(s.sent_path("host-a"), "w") as f:
            f.write("{")
        self.assertRaises(s.StateUnreadable, s.forget, "host-a", self.file)

    def test_a_once_written_a_moment_late_is_served_once(self):
        write(self.file, [{"name": "soon", "when": {"once": "2026-10-06 08:00"}, "do": {"message": "m"}},
                          {"name": "old", "when": {"once": "2026-10-06 07:00"}, "do": {"message": "m"}}])
        r = self.round("2026-10-06 08:01")
        self.assertEqual(self.serve(r, "2026-10-06 08:01"), ["soon@08:00"])
        self.assertEqual(len([p for p in r["problems"] if "old" in p[1]]), 1)
        r = self.round("2026-10-06 08:02")
        self.assertEqual((r["due"], r["problems"]), ([], []))

    def test_a_clock_that_jumped_ahead_and_came_back(self):
        write(self.file, [{"name": "morning", "when": {"at": "08:00"}, "do": {"message": "m"}}])
        self.round("2026-10-06 07:00")
        self.assertEqual(self.serve(self.round("2026-10-09 08:00"), "2026-10-09 08:00"), ["morning@08:00"])   # the clock jumped
        r = self.round("2026-10-06 07:30")                                  # and came back
        self.assertEqual((r["due"], [d["item"]["name"] for d in r["fresh"]], len(r["problems"])), ([], ["morning"], 1))
        self.assertEqual(self.round("2026-10-06 07:31")["problems"], [])    # said once
        self.assertEqual(self.serve(self.round("2026-10-06 08:00"), "2026-10-06 08:00"), ["morning@08:00"])

    def test_items_kept_out_of_the_scheduler_are_listed_and_never_due(self):
        write(self.file, [{"name": "sync", "scheduler": False, "why": "stays in cron", "when": {"every": "1m"}, "do": {"run": "x sync"}},
                          {"name": "collect", "when": {"every": "1m"}, "do": {"run": "x"}}])
        self.round("2026-10-06 08:00")
        r = self.round("2026-10-06 08:01")
        self.assertEqual(([d["item"]["name"] for d in r["due"]], [d["item"]["name"] for d in r["not_mine"]]), (["collect"], ["sync"]))
        found, _ = s.record_only([self.file], "host-a", now=at(TAIPEI, "2026-10-06 08:05"), tz=TAIPEI)
        self.assertEqual(found, [])                                         # first sight for the record-only notes
        found, _ = s.record_only([self.file], "host-a", now=at(TAIPEI, "2026-10-06 08:06"), tz=TAIPEI)
        self.assertEqual([d["item"]["name"] for d in found], ["collect"])
        write(self.file, [{"name": "sync", "when": {"every": "1m"}, "do": {"run": "x"}}])
        self.assertEqual(self.round("2026-10-06 09:00")["due"], [])         # taken over now: new, not made up for

    def test_of_two_rounds_at_once_one_serves(self):
        self.round("2026-10-06 07:59")
        a, b = self.round("2026-10-06 08:00"), self.round("2026-10-06 08:00")
        self.assertEqual([d["item"]["name"] for d in a["due"]], [d["item"]["name"] for d in b["due"]])
        self.assertEqual((self.serve(a, "2026-10-06 08:00"), self.serve(b, "2026-10-06 08:00")), (["morning@08:00"], []))

    def test_switched_off_and_on_again_is_not_made_up_for(self):
        on = [{"name": "morning", "when": {"at": "08:00"}, "do": {"message": "m"}}]
        write(self.file, on)
        self.round("2026-10-06 07:00")
        write(self.file, [dict(on[0], enabled=False)])
        self.assertEqual(self.round("2026-10-06 07:30")["due"], [])
        write(self.file, on)
        self.assertEqual(self.round("2026-10-06 09:00")["due"], [])        # was off at 08:00
        self.assertEqual(self.serve(self.round("2026-10-07 08:00"), "2026-10-07 08:00"), ["morning@08:00"])


class RecordOnly(Home):
    def setUp(self):
        super().setUp()
        self.file = write(os.path.join(self.home, "插件 一", "schedule.json"), {"items": [
            {"name": "morning", "when": {"at": "08:00"}, "do": {"message": "m"}},
            {"name": "collect", "when": {"every": "30m"}, "do": {"run": "would-be-a-disaster", "log": "~/.cache/x 紀錄.out"}}]})

    def lines(self):
        with open(s.log_path("host-a"), encoding="utf-8") as f:
            return [json.loads(line) for line in f]

    def test_writes_what_would_run_once_and_runs_nothing(self):
        t = at(TAIPEI, "2026-10-06 08:00")
        with mock.patch("subprocess.Popen") as popen, mock.patch("subprocess.run") as run, mock.patch("os.system") as system:
            first, _ = s.record_only([self.file], "host-a", now=t - 60, tz=TAIPEI)
            found, problems = s.record_only([self.file], "host-a", now=t + 20, tz=TAIPEI)
            again, _ = s.record_only([self.file], "host-a", now=t + 50, tz=TAIPEI)
            later, _ = s.record_only([self.file], "host-a", now=t + 1800, tz=TAIPEI)
        self.assertFalse(popen.called or run.called or system.called)
        self.assertEqual((first, [d["item"]["name"] for d in found], again, [d["item"]["name"] for d in later]),
                         ([], ["morning", "collect"], [], ["collect"]))
        self.assertEqual([(e["item"], e["do"], e["slot"], e["late"], e["log"]) for e in self.lines()],
                         [("morning", "message", "2026-10-06T08:00:00+08:00", 20, None),
                          ("collect", "run", "2026-10-06T08:00:00+08:00", 20, "~/.cache/x 紀錄.out"),
                          ("collect", "run", "2026-10-06T08:30:00+08:00", 0, "~/.cache/x 紀錄.out")])
        self.assertFalse(os.path.exists(s.sent_path("host-a")))          # the real notes are not touched
        self.assertEqual({e["state"] for e in s.read_state("host-a", dry=True).values()}, {"done"})
        with open(self.file, encoding="utf-8") as f:
            self.assertEqual(len(json.load(f)["items"]), 2)              # the schedule file is never written

    def test_a_damaged_state_is_neither_served_again_nor_replaced(self):
        t = at(TAIPEI, "2026-10-06 08:00")
        s.record_only([self.file], "host-a", now=t - 60, tz=TAIPEI)
        s.record_only([self.file], "host-a", now=t, tz=TAIPEI)
        path = s.sent_path("host-a", dry=True)
        good = self.raw(dry=True)
        for body in (good[:40], "", "[]\n", good.replace('"slot": %d' % t, '"slot": "garbage"')):
            with open(path, "w") as f:
                f.write(body)
            found, problems = s.record_only([self.file], "host-a", now=t + 60, tz=TAIPEI)
            self.assertEqual((found, problems[0][0], self.raw(dry=True)), ([], path, body))
        self.assertEqual(len(self.lines()), 2 + 4)                        # two served before, four rounds given up
        self.assertEqual(len([e for e in self.lines() if e.get("skipped") == "state"]), 4)

    def test_machine_offset_goes_by_the_host_given(self):
        spread = write(os.path.join(self.home, "spread.json"), [
            {"name": "sync", "when": {"every": "15m", "offset": "machine"}, "do": {"run": "r"}}])
        offset = s.machine_offset(900, "host-a")
        start = at(TAIPEI, "2026-10-06 09:00")
        s.record_only([spread], "host-a", now=start - 1, tz=TAIPEI, host="host-a")
        found, _ = s.record_only([spread], "host-a", now=start + offset, tz=TAIPEI, host="host-a")
        self.assertEqual([d["slot"] for d in found], [start + offset])
        self.assertFalse(os.path.exists(os.path.join(self.home, ".cache")))  # the log path is only carried along

    def test_problems_are_logged_and_the_zone_comes_from_the_setting(self):
        bad = write(os.path.join(self.home, "bad.json"), "nope")
        with zone_setting("Asia/Taipei"):
            found, problems = s.record_only([bad], "host-a", now=at(TAIPEI, "2026-10-06 08:00"))
        self.assertEqual([p[0] for p in problems], [bad])
        self.assertEqual([e["file"] for e in self.lines()], [bad])
        with zone_setting("Europe/Berlin"):
            s.record_only([self.file], "host-b", now=at(BERLIN, "2026-10-06 07:59"))
            found, _ = s.record_only([self.file], "host-b", now=at(BERLIN, "2026-10-06 08:00"))
        self.assertEqual(found[0]["item"]["name"], "morning")

    def test_a_claim_that_cannot_be_written_is_reported_and_the_round_is_not_a_good_one(self):
        t = at(TAIPEI, "2026-10-06 08:00")
        s.record_only([self.file], "host-a", now=t - 60, tz=TAIPEI)
        self.assertEqual(s.last_good_round("host-a", dry=True), t - 60)
        real_update = s._update

        def failing(machine, dry, change):
            raise OSError(28, "No space left on device")
        with mock.patch.object(s, "_update", side_effect=failing):
            found, problems = s.record_only([self.file], "host-a", now=t, tz=TAIPEI)
        self.assertEqual(found, [])
        self.assertEqual(len([p for p in problems if "沒有領到" in p[1]]), 2)
        self.assertEqual(s.last_good_round("host-a", dry=True), t - 60)     # that round does not count
        self.assertIs(s._update, real_update)
        found, _ = s.record_only([self.file], "host-a", now=t + 60, tz=TAIPEI)
        self.assertEqual([d["item"]["name"] for d in found], ["morning", "collect"])   # nobody had them: served now
        self.assertEqual(s.last_good_round("host-a", dry=True), t + 60)


class Watchman(Home):
    def test_last_good_round_is_kept_in_the_state_folder_and_beside_it(self):
        self.assertIsNone(s.last_good_round("host-a"))
        self.assertEqual(s.round_done("host-a", now=1000), 2)
        inside, beside = s.round_paths("host-a")
        self.assertEqual(os.path.dirname(inside), s.state_dir())
        self.assertEqual(os.path.dirname(beside), os.path.join(self.home, "beside"))
        self.assertEqual((s.last_good_round("host-a"), s.last_good_round("host-b"), s.last_good_round("host-a", dry=True)), (1000, None, None))
        s.round_done("host-a", now=2000)
        os.unlink(inside)                                                   # the state folder is out of reach
        self.assertEqual(s.last_good_round("host-a"), 2000)
        s.round_done("host-a", now=3000)
        os.unlink(beside)                                                   # the machine restarted
        self.assertEqual(s.last_good_round("host-a"), 3000)
        with open(inside, "w") as f:
            f.write("{")
        self.assertIsNone(s.last_good_round("host-a"))
        with mock.patch.object(s, "state_dir", return_value=os.path.join(self.home, "no", "such")), \
                mock.patch.object(s.os, "makedirs", side_effect=OSError(30, "Read-only file system")):
            self.assertEqual(s.round_done("host-a", now=4000), 0)           # nowhere to write: no exception

    def test_the_place_beside_must_be_this_user_s_own(self):
        with mock.patch.dict(os.environ, {"ALT_RUNTIME": os.path.join(self.home, "mine 的")}):
            self.assertEqual(s.beside_dir(), os.path.join(self.home, "mine 的"))
            self.assertEqual(os.stat(s.beside_dir()).st_mode & 0o777, 0o700)
        target = os.path.join(self.home, "target")
        os.makedirs(target)
        os.symlink(target, os.path.join(self.home, "link"))
        with mock.patch.dict(os.environ, {"ALT_RUNTIME": os.path.join(self.home, "link")}):
            self.assertIsNone(s.beside_dir())
            self.assertEqual(s.round_done("host-a", now=1000), 1)           # the state folder still gets it
        with mock.patch.dict(os.environ, {"ALT_RUNTIME": ""}), mock.patch.object(s, "beside_default", return_value="/tmp/x"), \
                mock.patch.object(s.os, "makedirs"), mock.patch.object(s.os, "lstat", return_value=mock.Mock(st_uid=os.getuid() + 1)):
            self.assertIsNone(s.beside_dir())                               # someone else made that folder first

    def test_without_alt_runtime_the_place_beside_is_this_homes_own(self):
        """Manager 2026-10-08: by the uid alone, /tmp/altention-<uid> was every home's; a throwaway home that did
        not set ALT_RUNTIME wrote over the real round's mark. Now each home has a folder of its own."""
        import hashlib
        other = os.path.join(self.home, "另一個 家")
        os.makedirs(other)
        link = os.path.join(self.home, "a link to the home")
        os.symlink(self.home, link)
        with mock.patch.dict(os.environ, {"HOME": self.home}):
            mine = s.beside_default()
        with mock.patch.dict(os.environ, {"HOME": other}):
            theirs = s.beside_default()
        with mock.patch.dict(os.environ, {"HOME": link}):
            same = s.beside_default()
        want = "/tmp/altention-%d-%s" % (os.getuid(), hashlib.sha256(os.fsencode(os.path.realpath(self.home))).hexdigest()[:8])
        self.assertEqual(mine, want)
        self.assertNotEqual(theirs, mine, "another home of the same user: another folder")
        self.assertEqual(same, mine, "the same home by another name: the same folder")
        self.assertRegex(theirs, r"^/tmp/altention-%d-[0-9a-f]{8}$" % os.getuid())
        self.assertNotEqual(mine, "/tmp/altention-%d" % os.getuid(), "the folder every home shared is no longer used")
        own = os.path.join(self.home, "stands in for /tmp", "altention-1-abcdef01")
        os.makedirs(os.path.dirname(own))
        with mock.patch.dict(os.environ, {"ALT_RUNTIME": ""}), mock.patch.object(s, "beside_default", return_value=own):
            self.assertEqual(s.beside_dir(), own, "with no ALT_RUNTIME, the writer and the reader both go by beside_default")
            self.assertEqual(os.stat(own).st_mode & 0o777, 0o700)
            self.assertEqual(s.round_done("host-a", now=1000, dry=True), 2)
            self.assertTrue(os.path.exists(os.path.join(own, "scheduler-round-dry.host-a.json")))
            self.assertEqual(s.last_good_round("host-a", dry=True, now=1001), 1000)


class Switch(Home):
    def setUp(self):
        super().setUp()
        self.file = write(os.path.join(self.home, "插件 一", "schedule.json"), [
            {"name": "morning", "when": {"at": "08:00"}, "do": {"message": "m"}},
            {"name": "noon", "when": {"at": "12:00"}, "do": {"message": "m"}},
            {"name": "collect", "when": {"every": "10m"}, "do": {"run": "x"}, "late": "1h"},
            {"name": "off", "enabled": False, "when": {"at": "09:00"}, "do": {"message": "m"}}])
        self.t = lambda text: at(TAIPEI, text)
        s.record_only([self.file], "host-a", now=self.t("2026-10-06 07:00"), tz=TAIPEI)
        s.record_only([self.file], "host-a", now=self.t("2026-10-06 07:10"), tz=TAIPEI)   # then the mode stopped

    def test_preview_lists_what_would_be_taken_for_missed(self):
        now = self.t("2026-10-06 09:05")
        got = s.switch_preview([self.file], "host-a", now, TAIPEI)
        self.assertEqual([(d["item"]["name"], TAIPEI.iso(d["slot"])[11:16]) for d in got],
                         [("morning", "08:00"), ("collect", "09:00")])
        self.assertFalse(os.path.exists(s.sent_path("host-a")))           # nothing written
        self.assertEqual(s.switch_preview([self.file], "host-a", self.t("2026-10-06 07:10") + 30, TAIPEI), [])
        with open(s.sent_path("host-a", dry=True), "w") as f:
            f.write("{")
        self.assertRaises(s.StateUnreadable, s.switch_preview, [self.file], "host-a", now, TAIPEI)

    def test_adopting_counts_everything_as_done_up_to_the_switch(self):
        now = self.t("2026-10-06 09:05")
        self.assertEqual(s.adopt_dry("host-a", now, TAIPEI), 4)
        state = s.read_state("host-a")
        self.assertEqual({(e["slot"], e["state"]) for e in state.values()}, {(now, "done"), (now, "off")})
        r = s.start_round([self.file], "host-a", now=now + 60, tz=TAIPEI)
        self.assertEqual((r["due"], r["fresh"]), ([], []))                 # nothing made up for, nothing new
        r = s.start_round([self.file], "host-a", now=self.t("2026-10-06 09:10"), tz=TAIPEI)
        self.assertEqual([d["item"]["name"] for d in r["due"]], ["collect"])
        r = s.start_round([self.file], "host-a", now=self.t("2026-10-06 12:00"), tz=TAIPEI)
        self.assertEqual([d["item"]["name"] for d in r["due"]], ["noon", "collect"])
        self.assertEqual(s.adopt_dry("host-a", now, TAIPEI), 0)            # again: nothing more to take

    def test_after_adopting_the_preview_is_empty(self):
        now = self.t("2026-10-06 09:05")
        self.assertEqual(len(s.switch_preview([self.file], "host-a", now, TAIPEI)), 2)
        s.adopt_dry("host-a", now, TAIPEI)
        self.assertEqual(s.switch_preview([self.file], "host-a", now, TAIPEI), [])   # how a switch is checked
        self.assertEqual(s.switch_preview([self.file], "host-a", now + 240, TAIPEI), [])
        later = s.switch_preview([self.file], "host-a", self.t("2026-10-06 09:25"), TAIPEI)   # nobody ran since
        self.assertEqual([(d["item"]["name"], TAIPEI.iso(d["slot"])[11:16]) for d in later], [("collect", "09:20")])

    def test_a_later_real_note_stays_and_unreadable_files_stop_it(self):
        now = self.t("2026-10-06 09:05")
        key = s.sent_key(self.file, "noon")
        s.mark_sent("host-a", {key: now + 3600}, TAIPEI)
        self.assertEqual(s.adopt_dry("host-a", now, TAIPEI), 3)
        self.assertEqual(s.read_sent("host-a")[key], now + 3600)
        before = self.raw()
        with open(s.sent_path("host-a", dry=True), "w") as f:
            f.write("")
        self.assertRaises(s.StateUnreadable, s.adopt_dry, "host-a", now + 60, TAIPEI)
        self.assertEqual(self.raw(), before)


if __name__ == "__main__":
    unittest.main()
