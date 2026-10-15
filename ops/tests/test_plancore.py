"""ops/plancore.py: plugin.json written out as cron lines and hooks, the real ones read back, compared by content."""
import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))))
from altention import testing as _testing  # noqa: E402
_testing.guard_home()  # tests never touch the real home (altention/testing.py)
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import plancore  # noqa: E402

HOME = os.path.expanduser("~")


class Time(unittest.TestCase):
    def test_cron_fields(self):
        t = plancore.cron_time
        self.assertEqual(t({"at": "08:00", "days": "daily"}, "host-a"), "0 8 * * *")
        self.assertEqual(t({"at": "21:00", "days": ["sun"]}, "host-a"), "0 21 * * 0")
        self.assertEqual(t({"at": "07:05", "days": ["fri", "mon"]}, "host-a"), "5 7 * * 1,5")
        self.assertEqual(t({"every": "10m"}, "host-a"), "*/10 * * * *")
        self.assertEqual(t({"every": "1h", "offset": "7m"}, "host-a"), "7 * * * *")
        self.assertEqual(t({"every": "15m", "offset": "machine"}, "host-a"), "10,25,40,55 * * * *")  # md5 of host-a
        self.assertEqual(t({"every": "15m", "offset": "machine"}, None), "*/15 * * * *", "no stable name: 0")
        self.assertEqual(t("@reboot", "host-a"), "@reboot")
        self.assertEqual(t({"every": "2h", "offset": "90m"}, "host-a"), "30 1-23/2 * * *")
        for bad in ({"every": "7m"}, {"every": "10m", "offset": "15m"}, {"at": "8:5"}, {"at": "24:00"}):
            with self.assertRaises(ValueError, msg=bad):
                t(bad, "host-a")


class Words(unittest.TestCase):
    def test_quoting_and_home_do_not_matter(self):
        a = plancore.words('PROGRESS_CLAUDE="~/.local/bin/claude" /usr/bin/python3 "$HOME/x y/progress.py" run >>"~/.cache/o" 2>&1')
        b = plancore.words("PROGRESS_CLAUDE=%s/.local/bin/claude /usr/bin/python3 '%s/x y/progress.py' run >>%s/.cache/o 2>&1"
                           % (HOME, HOME, HOME))
        self.assertEqual(a, b)

    def test_the_comment_is_not_content_but_is_read(self):
        line = '0 8 * * * dcpost 100000000000000001 "⏰ 早報 #1" >>"~/o" 2>&1 # planner'
        self.assertEqual(plancore.comment(line), "# planner")
        time, command = plancore.split_cron(line)
        self.assertEqual(plancore.words(command)[-1], "2>&1")
        self.assertIn("⏰ 早報 #1", plancore.words(command), "a # inside quotes is the message's")

    def test_lines_that_are_not_jobs(self):
        for line in ("", "# a note", "SHELL=/bin/bash", "PATH=/usr/bin:/bin"):
            self.assertIsNone(plancore.split_cron(line), line)
        self.assertEqual(plancore.split_cron("@reboot cd x && y"), ("@reboot", "cd x && y"))


class Compare(unittest.TestCase):
    ENGINE = "/srv/engine 引擎"

    def want(self, time, command, kind="cron"):
        return {"plugin": "project-a", "name": "n", "kind": kind, "key": plancore.cron_key(time, command),
                "text": "%s %s" % (time, command), "scheduler": True}

    def test_same_time_differs_missing_extra(self):
        lines = ['*/10 * * * * /usr/bin/python3 "%s/progress/progress.py" run' % self.ENGINE,
                 '5 * * * * /usr/bin/python3 "%s/usage/collect.py" # usage' % self.ENGINE,
                 '0 3 * * * /usr/bin/python3 "%s/old/gone.py"' % self.ENGINE,
                 '0 4 * * * /usr/bin/python3 "%s/old/marked.py"  # altention:progress' % self.ENGINE,
                 '30 20 * * 1-5 cd $LAS && python3 -m scripts.evening  # another project: not ours']
        have, paused, unreadable = plancore.installed_cron(lines, self.ENGINE)
        self.assertEqual(len(have), 4, "the other project's line is not ours")
        want = [self.want("*/10 * * * *", "/usr/bin/python3 '%s/progress/progress.py' run" % self.ENGINE),
                self.want("7 * * * *", '/usr/bin/python3 "%s/usage/collect.py"' % self.ENGINE),
                self.want("0 8 * * *", '/usr/bin/python3 "%s/new.py"' % self.ENGINE)]
        got = [(s, w["key"][0] if w else None, note) for s, w, _, note in plancore.compare(want, have, [])]
        self.assertEqual(got, [("same", "*/10 * * * *", ""), ("time differs", "7 * * * *", "# usage"),
                               ("missing", "0 8 * * *", ""), ("unclaimed", None, ""), ("extra", None, "# altention:progress")],
                         "an unmarked line no item matches is unclaimed; only a new-marker one is extra")

    def test_a_second_line_for_the_same_item_is_a_duplicate(self):
        """An old install script run after the markers changed adds its line again (2026-10-06)."""
        lines = ['7 * * * * /usr/bin/python3 "%s/usage/collect.py"  # altention:usage' % self.ENGINE,
                 '7 * * * * /usr/bin/python3 "%s/usage/collect.py" # usage' % self.ENGINE]
        have, _, _ = plancore.installed_cron(lines, self.ENGINE)
        w = self.want("7 * * * *", '/usr/bin/python3 "%s/usage/collect.py"' % self.ENGINE)
        self.assertEqual([r[0] for r in plancore.compare([w], have, [])], ["same", "duplicate"])

    def test_hooks(self):
        settings = {"hooks": {"Stop": [{"hooks": [{"command": "'%s/dcbot/tools/dchook' --global" % self.ENGINE, "timeout": 20},
                                                   {"command": "other-tool", "timeout": 5}]}],
                              "PreToolUse": [{"matcher": "AskUserQuestion",
                                              "hooks": [{"command": '"%s/dcbot/tools/dchook" --global --ask' % self.ENGINE,
                                                         "timeout": 43200}]}]}}
        have = plancore.installed_hooks(settings, self.ENGINE)
        self.assertEqual(len(have), 2, "another tool's hook is not ours")
        key = ("PreToolUse", "AskUserQuestion", tuple(plancore.words("'%s/dcbot/tools/dchook' --global --ask" % self.ENGINE)), 43200)
        result = plancore.compare([{"plugin": "core", "name": "x", "kind": "hook", "key": key, "text": ""}], [], have)
        self.assertEqual([r[0] for r in result], ["same", "unclaimed"], "hooks carry no marker: never extra")


class OnlyOurs(unittest.TestCase):
    ENGINE = "/srv/engine"

    def test_other_lines_are_never_parsed_ours_that_cannot_be_are_said(self):
        lines = ["SHELL=/bin/bash", "5 4 * * * echo don't >> /tmp/x", "# a note with an ' quote",
                 '0 1 * * * /usr/bin/python3 "%s/progress/progress.py run' % self.ENGINE]
        have, paused, unreadable = plancore.installed_cron(lines, self.ENGINE)
        self.assertEqual((have, paused), ([], []))
        self.assertEqual(unreadable, ['0 1 * * * /usr/bin/python3 "%s/progress/progress.py run' % self.ENGINE])

    def test_what_may_be_ours_is_narrow(self):
        """#code-review 2026-10-06: these four are someone else's."""
        for line in ("0 1 * * * /opt/acct/report.sh --month   # usage report",
                     "30 3 * * * tar czf /backup/a.tgz %s-backup" % self.ENGINE,
                     "15 3 * * * rsync -a %s-old/ /backup/" % self.ENGINE,
                     "0 2 * * * echo planner is # not a marker here"):
            self.assertFalse(plancore.ours(line, self.ENGINE), line)
        for line in ("0 2 * * * /opt/x/run.sh # planner", '7 * * * * python3 "%s/usage/collect.py"' % self.ENGINE,
                     "0 5 * * * anything  # altention:usage"):
            self.assertTrue(plancore.ours(line, self.ENGINE), line)
        self.assertEqual(plancore.marked("0 5 * * * anything  # altention:usage"), "usage")
        self.assertEqual((plancore.marked("0 5 * * * x  # altention:planner/morning"),
                          plancore.marked_name("0 5 * * * x  # altention:planner/morning"),
                          plancore.marked_name("0 5 * * * x  # altention:planner")), ("planner", "morning", None))
        self.assertEqual(plancore.marker({"plugin": "planner", "name": "morning"}), "planner/morning")
        self.assertEqual(plancore.marker({"plugin": "planner", "name": "早報 1"}), "planner", "a name the marker cannot hold")
        self.assertIsNone(plancore.marked("0 5 * * * anything # planner"))

    def test_ours_commented_out_is_paused_not_missing(self):
        line = '# */10 * * * * /usr/bin/python3 "%s/progress/progress.py" run' % self.ENGINE
        have, paused, _ = plancore.installed_cron([line], self.ENGINE)
        want = [{"plugin": "progress", "name": "run", "kind": "cron", "text": "",
                 "key": plancore.cron_key("*/10 * * * *", '/usr/bin/python3 "%s/progress/progress.py" run' % self.ENGINE)}]
        self.assertEqual([r[0] for r in plancore.compare(want, have, [], paused)], ["paused"])

    def test_the_engine_through_home(self):
        home = os.path.expanduser("~")
        line = '7 * * * * /usr/bin/python3 "$HOME/engine/usage/collect.py"'
        have, _, _ = plancore.installed_cron([line], os.path.join(home, "engine"))
        self.assertEqual(len(have), 1)


class Read(unittest.TestCase):
    def run_as(self, code, out="", err="", exc=None):
        def fake(*a, **k):
            if exc:
                raise exc
            return plancore.subprocess.CompletedProcess(a, code, out, err)
        return lambda: plancore.read_crontab(run=fake)

    def test_only_no_crontab_is_empty(self):
        self.assertEqual(self.run_as(1, err="no crontab for alice")(), [])
        self.assertEqual(self.run_as(0, out="a\nb\n")(), ["a", "b"])
        for case in (self.run_as(1, err="crontab: permission denied"), self.run_as(1),
                     self.run_as(0, exc=FileNotFoundError("crontab")),
                     self.run_as(0, exc=plancore.subprocess.TimeoutExpired("crontab", 30))):
            with self.assertRaises(plancore.Unknown):
                case()

    def test_an_unreadable_settings_json_is_unknown(self):
        path = os.path.expanduser("~/.claude/settings.json")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        try:
            with open(path, "w") as f:
                f.write("{half")
            with self.assertRaises(plancore.Unknown):
                plancore.read_settings()
            with open(path, "w") as f:
                f.write("[]")
            with self.assertRaises(plancore.Unknown):
                plancore.read_settings()
            os.remove(path)
            self.assertEqual(plancore.read_settings(), {})
        finally:
            if os.path.exists(path):
                os.remove(path)


class Render(unittest.TestCase):
    def test_a_message_item_is_todays_dcpost_line(self):
        d = {"channels": [{"name": "secretary", "setting": "planner.channel"}]}
        s = {"name": "morning", "when": {"at": "08:00", "days": "daily"},
             "do": {"message": "⏰ 每日早報", "channel": "secretary", "log": "~/.cache/planner.out"}}
        time, command = plancore.render_schedule("planner", "/p", d, s, {"planner": {"channel": "100000000000000001"}}, "host-a")
        self.assertEqual(time, "0 8 * * *")
        w = plancore.words(command)
        self.assertTrue(w[0].endswith("/bin/altention"))
        self.assertEqual(w[1:], ["dc", "post", "100000000000000001", "⏰ 每日早報", ">>%s/.cache/planner.out" % HOME, "2>&1"])

    def test_an_item_that_cannot_be_written_out_is_not_compared(self):
        rows = [("planner", "/p", {"name": "planner", "schedules": [
            {"name": "remind", "when": {"every": "5m"}, "do": {"run": "remind.py {setting:planner.files}"}}]}, "on", "")]
        want, unknown = plancore.wanted(rows, "host-a", {}, "host-a")
        self.assertEqual(want, [])
        self.assertEqual(unknown[0][0], "planner/remind")

    def test_unknown_plugins_are_not_compared(self):
        want, unknown = plancore.wanted([("progress", "/p", None, "unknown", "設定讀不出來")], "host-a", None, None)
        self.assertEqual((want, unknown), ([], [("progress", "設定讀不出來")]))


if __name__ == "__main__":
    unittest.main()


class After(unittest.TestCase):
    def test_after_moves_the_machines_minute_and_wraps(self):
        base = plancore.machine_minute(15, "box")
        self.assertEqual(plancore.cron_time({"every": "15m", "offset": "machine"}, "box"),
                         ",".join(str(x) for x in range(base, 60, 15)) + " * * * *" if base else "*/15 * * * *")
        moved = (base + 8) % 15
        self.assertEqual(plancore.cron_time({"every": "15m", "offset": "machine", "after": "8m"}, "box"),
                         ",".join(str(x) for x in range(moved, 60, 15)) + " * * * *" if moved else "*/15 * * * *")
