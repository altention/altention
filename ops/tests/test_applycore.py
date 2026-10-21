"""ops/applycore.py and plugins.py apply: writing plan's result into a crontab and settings.json, with a fake crontab
command in a throwaway home. The crontab is bytes end to end: other people's lines stay byte for byte (form feeds,
U+2028, lone carriage returns, bytes that are not UTF-8 included); nothing but new-marker lines is ever removed; a
hook is never removed; the guards hold; nothing is written when something cannot be read, nor without --write."""
import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))))
from altention import testing as _testing  # noqa: E402
_testing.guard_home()  # tests never touch the real home (altention/testing.py)
import copy
import io
import json
import os
import shutil
import subprocess
import stat
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import applycore  # noqa: E402
import plancore  # noqa: E402
import plugins  # noqa: E402

ENGINE = "/srv/engine 引擎"
FAKE = """#!/bin/sh
# a crontab of our own: -l prints the file as it is (or says there is none), a path installs it as it is
F="$FAKE_CRONTAB"
if [ "$1" = "-l" ]; then
  [ -n "$FAKE_FAIL" ] && { echo "crontab: permission denied" >&2; exit 1; }
  [ -f "$F" ] || { echo "no crontab for alice" >&2; exit 1; }
  cat "$F"; exit 0
fi
[ -n "$FAKE_REFUSE" ] && { echo "crontab: cannot install" >&2; exit 1; }
[ "$1" = "-" ] || { echo "crontab: only stdin here (apply writes through crontab -)" >&2; exit 2; }
cat > "$F"
"""
OTHERS = [b"SHELL=/bin/bash", b"PATH=/usr/bin:/bin", b"# someone's note with an ' quote", b"",
          b"30 20 * * 1-5  cd $LAS && echo don't stop >> logs/x 2>&1",
          b"0 1 * * * /opt/acct/report.sh --month   # usage report",
          b"0 3 * * * /opt/x/say 'a\x0cb' >> /tmp/x",                      # a form feed
          "0 3 * * * /opt/x/say 'a b\u0085c'".encode("utf-8"),        # line and next-line separators
          b"0 4 * * * /opt/x/say 'a\rb'",                                  # a carriage return inside
          b"0 5 * * * /opt/x/caf\xe9.sh",                                  # Latin-1, not UTF-8
          b"0 6 * * * /opt/x/crlf.sh\r"]                                   # a CRLF ending
PROGRESS = ('*/10 * * * * /usr/bin/python3 "%s/progress/progress.py" run' % ENGINE).encode("utf-8")
USAGE = ('7 * * * * /usr/bin/python3 "%s/usage/collect.py" >>"~/.cache/usage.out" 2>&1 # usage' % ENGINE).encode("utf-8")


def want(plugin_name, name, time, command, kind="cron"):
    return {"plugin": plugin_name, "name": name, "kind": kind, "key": plancore.cron_key(time, command),
            "text": "%s %s" % (time, command), "scheduler": True}


def wants():
    return [want("progress", "run", "*/10 * * * *", '/usr/bin/python3 "%s/progress/progress.py" run' % ENGINE),
            want("usage", "collect", "7 * * * *", '/usr/bin/python3 "%s/usage/collect.py" >>"~/.cache/usage.out" 2>&1' % ENGINE)]


class Rig(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="家 apply ")
        bindir = os.path.join(self.tmp, "bin")
        os.makedirs(bindir)
        with open(os.path.join(bindir, "crontab"), "w") as f:
            f.write(FAKE)
        os.chmod(os.path.join(bindir, "crontab"), 0o755)
        self.file = os.path.join(self.tmp, "the crontab")
        self.env = mock.patch.dict(os.environ, {"PATH": bindir + ":/usr/bin:/bin", "FAKE_CRONTAB": self.file})
        self.env.start()
        applycore.MACHINE[0] = "host-a"

    def tearDown(self):
        self.env.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)
        shutil.rmtree(os.path.dirname(applycore.state_root()), ignore_errors=True)

    def put(self, lines):
        with open(self.file, "wb") as f:
            f.write(plancore.join_lines(lines))

    def get(self):
        with open(self.file, "rb") as f:
            return plancore.raw_lines(f.read())

    def apply_once(self, lines, wanted=(), rows=(), allow_time=(), away=()):
        self.put(lines)
        raw = plancore.read_crontab_raw()
        texts = [t for t in (plancore.text(l) for l in plancore.raw_lines(raw)) if t is not None]
        have, paused, _ = plancore.installed_cron(texts, ENGINE)
        result = plancore.compare(list(wanted), have, [], paused, list(away))
        new, actions, removed, kept = applycore.new_crontab(plancore.raw_lines(raw), result, list(rows), allow_time)
        backup = applycore.make_backup(raw, None)
        return applycore.write_crontab(raw, new, removed, kept, backup), actions


class Bytes(Rig):
    def test_other_peoples_lines_stay_byte_for_byte_whatever_they_hold(self):
        wrote, _ = self.apply_once(OTHERS + [PROGRESS, USAGE], wants())
        self.assertTrue(wrote)
        after = self.get()
        self.assertEqual([l for l in after if l in OTHERS], OTHERS, "same bytes, same order")
        self.assertEqual(len(after), len(OTHERS) + 2)
        self.assertEqual(after[-2], PROGRESS + b"  # altention:progress/run", "the command's bytes, then the marker")
        self.assertEqual(after[-1], USAGE[:-len(b" # usage")] + b"  # altention:usage/collect")

    def test_the_backup_is_crontab_l_as_it_printed_it(self):
        self.put(OTHERS + [PROGRESS])
        raw = plancore.read_crontab_raw()
        folder = applycore.make_backup(raw, None)
        with open(os.path.join(folder, "crontab"), "rb") as f:
            self.assertEqual(f.read(), raw)
        self.assertEqual(stat.S_IMODE(os.stat(folder).st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(os.stat(os.path.join(folder, "crontab")).st_mode), 0o600)


class Rules(Rig):
    def test_a_missing_line_is_added_at_the_end(self):
        self.apply_once(OTHERS, wants())
        self.assertEqual(self.get()[len(OTHERS):], [(w["text"] + "  # altention:%s/%s" % (w["plugin"], w["name"])).encode("utf-8") for w in wants()])

    def test_comments_of_many_kinds_three_runs_then_nothing_to_change(self):
        """manager 2026-10-06: the second and third runs must have nothing to change, and no word is cut."""
        variants = [b" #usage", b" #  usage", b"\t# usage  ", b"", b" # paused  twice", b" # 'quoted' note"]
        for tail in variants:
            line = USAGE[:-len(b" # usage")] + tail
            self.apply_once(OTHERS + [line], wants()[1:])
            first = self.get()
            for _ in range(2):
                wrote, _ = self.apply_once(first, wants()[1:])
                self.assertFalse(wrote, tail)
            self.assertTrue(first[-1].endswith(b"  # altention:usage/collect"), tail)
            self.assertIn(USAGE[:-len(b" # usage")], first[-1], "the command untouched")
            if tail.strip(b" \t") not in (b"#usage", b"#  usage", b"# usage", b""):
                self.assertIn(tail.strip(), first[-1], "someone's comment kept whole")
        quoted = ('0 9 * * * echo "#1 done" "%s/progress/x"' % ENGINE).encode("utf-8")
        self.apply_once(OTHERS + [quoted], [])
        self.assertEqual(self.get(), OTHERS + [quoted], "a # inside quotes is no comment; unclaimed, left alone")

    def planner(self, channel, times=("0 8 * * *", "0 22 * * *", "0 21 * * 0")):
        tool = '"%s/bin/altention"' % ENGINE
        return [want("planner", n, t, "%s dc post %s '%s：照 CLAUDE.md' >>\"~/.cache/planner.out\" 2>&1" % (tool, channel, n))
                for n, t in zip(("morning", "evening", "weekly"), times)]

    def test_a_channel_moved_leaves_one_line_per_item(self):
        """Owner 2026-10-15: planner.channel moved from #chat to #secretary; apply added the three new lines and kept
        the three old ones (marked with the plugin alone), so both channels got the briefing."""
        old = [("%s  # altention:planner" % w["text"]).encode("utf-8") for w in self.planner("11")]
        mine = b"0 8 * * * /opt/me/brief.sh  # not ours"
        wrote, actions = self.apply_once(OTHERS + old + [mine], self.planner("22"))
        self.assertTrue(wrote)
        after = self.get()
        self.assertEqual(after[:len(OTHERS)], OTHERS)
        self.assertEqual(after[len(OTHERS):], [("%s  # altention:planner/%s" % (w["text"], w["name"])).encode("utf-8")
                                               for w in self.planner("22")] + [mine], "in the old lines' places")
        self.assertFalse(any(b" 11 " in l for l in after), "no line for the old channel")
        self.assertTrue(any("換掉" in a for a in actions))
        wrote, _ = self.apply_once(after, self.planner("22"))
        self.assertFalse(wrote, "then nothing to change")

    def test_a_named_line_is_replaced_whatever_changed(self):
        old = [("%s  # altention:planner/%s" % (w["text"], w["name"])).encode("utf-8") for w in self.planner("22")]
        moved = self.planner("33", ("0 9 * * *", "30 21 * * *", "0 20 * * 0"))  # the channel and the times
        self.apply_once(OTHERS + old, moved)
        self.assertEqual(self.get()[len(OTHERS):], [("%s  # altention:planner/%s" % (w["text"], w["name"])).encode("utf-8")
                                                    for w in moved])

    def test_not_surely_the_same_item_is_left_and_the_new_line_added(self):
        """A line marked with the plugin alone is taken for an item only when nothing else could be meant; a line of
        another plugin, or with no marker, is never taken."""
        w = self.planner("22")[:1]
        other = [("%s  # altention:usage" % w[0]["text"].replace(" 22 ", " 11 ")).encode("utf-8"),  # another plugin's marker
                 w[0]["text"].replace(" 22 ", " 11 ").encode("utf-8")]       # no marker: someone's copy
        self.apply_once(OTHERS + other, w)
        self.assertEqual(self.get(), OTHERS + other + [("%s  # altention:planner/morning" % w[0]["text"]).encode("utf-8")])
        two = [("%s  # altention:planner" % x["text"]).encode("utf-8") for x in self.planner("11", ("0 8 * * *",) * 3)]
        wants = self.planner("22", ("0 8 * * *",) * 3)  # three items at one time: which old line is which cannot be told
        self.apply_once(OTHERS + two, wants)
        self.assertEqual(self.get()[:len(OTHERS) + 3], OTHERS + two, "left as they are")
        self.assertEqual(len(self.get()), len(OTHERS) + 6)

    def test_the_table_goes_through_stdin(self):
        seen = []

        def run(argv, **kw):
            seen.append((argv, kw.get("input")))
            return subprocess.CompletedProcess(argv, 0, b"", b"")
        applycore.install_crontab(b"0 1 * * * x\n", run)
        self.assertEqual(seen, [(["crontab", "-"], b"0 1 * * * x\n")], "no file path to be cut short")

    def test_an_old_looking_line_that_matches_nothing_exactly_is_left_alone(self):
        odd = ('*/10 * * * * /usr/bin/python3 "%s/progress/progress.py" run --other # planner' % ENGINE).encode("utf-8")
        wrote, _ = self.apply_once(OTHERS + [odd], [])
        self.assertFalse(wrote)
        self.assertEqual(self.get(), OTHERS + [odd])

    def test_only_a_new_marker_line_of_a_plugin_plainly_off_is_removed(self):
        gone = ('0 3 * * * /usr/bin/python3 "%s/progress/progress.py" old  # altention:progress' % ENGINE).encode("utf-8")
        kept = ('0 4 * * * /usr/bin/python3 "%s/usage/x.py"  # altention:usage' % ENGINE).encode("utf-8")
        rows = [("progress", ENGINE + "/progress", {"name": "progress"}, "off", ""), ("usage", ENGINE + "/usage", {"name": "usage"}, "unknown", "x")]
        self.apply_once(OTHERS + [gone, kept], [], rows)
        self.assertEqual(self.get(), OTHERS + [kept])

    def test_time_differs_is_changed_only_when_named_and_only_the_time(self):
        old = ('4,19,34,49 * * * *  /usr/bin/python3 "%s/chatlog/chatlog.py" sync >/dev/null 2>&1' % ENGINE).encode("utf-8")
        w = [want("chatlog", "sync", "8,23,38,53 * * * *", '/usr/bin/python3 "%s/chatlog/chatlog.py" sync >/dev/null 2>&1' % ENGINE)]
        wrote, actions = self.apply_once(OTHERS + [old], w)
        self.assertFalse(wrote)
        self.apply_once(OTHERS + [old], w, allow_time=["chatlog/sync"])
        self.assertEqual(self.get()[-1], old.replace(b"4,19,34,49 * * * *  ", b"8,23,38,53 * * * * ") + b"  # altention:chatlog/sync")

    def test_paused_is_never_filled_in(self):
        paused = b"# " + PROGRESS
        wrote, _ = self.apply_once(OTHERS + [paused], wants()[:1])
        self.assertFalse(wrote)
        self.assertEqual(self.get(), OTHERS + [paused])

    def test_a_percent_is_escaped_for_cron(self):
        d = {"channels": []}
        s = {"name": "m", "when": {"at": "08:00"}, "do": {"message": "用量到 80% 了", "channel": "100000000000000001"}}
        time, command = plancore.render_schedule("usage", "/p", d, s, {}, "host-a")
        self.assertIn("80\\%", command)
        self.assertNotIn("80% ", command)


class Guards(Rig):
    def test_a_touching_a_line_not_meant_to_change_is_refused(self):
        self.put(OTHERS)
        raw = plancore.join_lines(OTHERS)
        with self.assertRaises(applycore.Refused):
            applycore.write_crontab(raw, OTHERS[:-1] + [b"changed"], 0, OTHERS, "b")
        self.assertEqual(self.get(), OTHERS)

    def test_b_losing_more_lines_than_meant_is_refused(self):
        self.put(OTHERS)
        with self.assertRaises(applycore.Refused):
            applycore.write_crontab(plancore.join_lines(OTHERS), OTHERS[:2], 1, OTHERS[:2], "b")
        self.assertEqual(self.get(), OTHERS)

    def test_c_no_backup_no_write(self):
        os.makedirs(applycore.state_root(), exist_ok=True)
        with open(applycore.backup_root(), "w") as f:
            f.write("a file where the folder goes")
        with self.assertRaises(applycore.Refused):
            applycore.make_backup(b"x\n", None)

    def test_a_crontab_changed_since_it_was_read_is_not_written_over(self):
        self.put(OTHERS)
        raw = plancore.read_crontab_raw()
        self.put(OTHERS + [b"15 4 * * * /home/alice/new-job.sh"])  # someone, meanwhile
        with self.assertRaises(applycore.Refused) as e:
            applycore.write_crontab(raw, OTHERS + [b"0 1 * * * ours"], 0, OTHERS, "b")
        self.assertIn("被改過", str(e.exception))
        self.assertEqual(self.get()[-1], b"15 4 * * * /home/alice/new-job.sh")

    def test_a_crontab_that_reads_back_wrong_is_put_back_and_that_checked(self):
        self.put(OTHERS)
        raw = plancore.join_lines(OTHERS)
        real = applycore.install_crontab
        with mock.patch.object(applycore, "install_crontab", lambda r, run=None: real(r + (b"# stray\n" if r != raw else b""))):
            with self.assertRaises(applycore.Refused) as e:
                applycore.write_crontab(raw, OTHERS + [b"0 1 * * * new"], 0, OTHERS, "the backup")
        self.assertIn("重讀確認過", str(e.exception))
        self.assertEqual(self.get(), OTHERS)

    def test_a_failed_put_back_is_said_with_where_the_backup_is(self):
        self.put(OTHERS)
        raw = plancore.join_lines(OTHERS)
        real = applycore.install_crontab
        calls = []

        def first_wrong_then_refused(r, run=None):
            calls.append(r)
            if len(calls) == 1:
                return real(r + b"# stray\n")
            raise applycore.Refused("crontab 寫不進去：cannot install")
        with mock.patch.object(applycore, "install_crontab", first_wrong_then_refused):
            with self.assertRaises(applycore.Refused) as e:
                applycore.write_crontab(raw, OTHERS + [b"0 1 * * * new"], 0, OTHERS, "/the/backup")
        self.assertIn("還原也失敗", str(e.exception))
        self.assertIn("/the/backup/crontab", str(e.exception))
        self.assertNotIn("已用備份", str(e.exception))

    def test_no_crontab_at_all_is_noted(self):
        folder = applycore.make_backup(b"", None)
        self.assertTrue(os.path.exists(os.path.join(folder, "crontab.absent")))

    def test_backups_never_collide_the_first_is_kept_for_good(self):
        a = applycore.make_backup(b"one\n", None, stamp="20260101-000000")
        b = applycore.make_backup(b"two\n", None, stamp="20260101-000000")  # the same second
        self.assertNotEqual(a, b)
        self.assertIn(applycore.FIRST, os.path.basename(a))
        with mock.patch.object(applycore, "BACKUP_KEEP", 2):
            for i in range(5):
                applycore.make_backup(b"n\n", None, stamp="2026010%d-000000" % (i + 2))
        names = sorted(os.listdir(applycore.backup_root()))
        self.assertIn(os.path.basename(a), names, "the backup before anything was written stays")
        self.assertEqual(len(names), 3)

    def test_leftover_temporary_files_are_cleaned_by_the_next_apply(self):
        os.makedirs(applycore.state_root(), exist_ok=True)
        left = os.path.join(applycore.state_root(), "tmpabc.crontab")
        with open(left, "w") as f:
            f.write("a whole crontab")
        with applycore.one_at_a_time():
            pass
        self.assertFalse(os.path.exists(left))

    def test_leftovers_go_at_the_start_of_any_apply_even_with_nothing_to_write(self):
        os.makedirs(applycore.state_root(), exist_ok=True)
        open(os.path.join(applycore.state_root(), "apply.lock"), "w").close()
        left = os.path.join(applycore.state_root(), "tmpxyz.crontab")
        open(left, "w").close()
        applycore.clean_if_free()
        self.assertFalse(os.path.exists(left))
        with applycore.one_at_a_time():  # while an apply runs, its file is left alone
            open(left, "w").close()
            applycore.clean_if_free()
            self.assertTrue(os.path.exists(left))

    def test_an_unwritable_state_folder_is_said(self):
        parent = os.path.dirname(os.path.dirname(applycore.state_root()))
        os.makedirs(parent, exist_ok=True)
        os.chmod(parent, 0o555)
        try:
            if os.access(parent, os.W_OK):
                self.skipTest("root writes anyway")
            with self.assertRaises(applycore.Refused) as e:
                with applycore.one_at_a_time():
                    pass
            self.assertIn("寫不進去", str(e.exception))
        finally:
            os.chmod(parent, 0o755)

    def test_one_apply_at_a_time(self):
        with applycore.one_at_a_time():
            with self.assertRaises(applycore.Refused):
                with applycore.one_at_a_time():
                    pass

    def test_paths_carry_the_machines_name(self):
        self.assertTrue(applycore.backup_root().endswith(os.path.join("host-a", "backup")))


class Settings(Rig):
    def setUp(self):
        super().setUp()
        self.path = os.path.expanduser("~/.claude/settings.json")
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        self.before = {"model": "x", "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "other-tool", "timeout": 5}]}]}}
        self.raw = json.dumps(self.before).encode("utf-8")
        with open(self.path, "wb") as f:
            f.write(self.raw)
        os.chmod(self.path, 0o600)

    def tearDown(self):
        if os.path.exists(self.path):
            os.remove(self.path)
        super().tearDown()

    def hook_want(self, event, matcher, command, timeout):
        return {"plugin": "core", "name": event, "kind": "hook", "text": command,
                "key": (event, matcher, tuple(plancore.words(command)), timeout)}

    def test_missing_hooks_are_added_nothing_else_changes_the_mode_stays(self):
        cmd = "'%s/dcbot/tools/dchook' --global" % ENGINE
        result = plancore.compare([self.hook_want("Stop", "", cmd, 20), self.hook_want("PreToolUse", "AskUserQuestion", cmd + " --ask", 43200)],
                                  [], plancore.installed_hooks(self.before, ENGINE))
        new, actions, added = applycore.new_settings(self.before, result)
        self.assertTrue(applycore.write_settings(self.raw, self.before, new, added, "b"))
        with open(self.path) as f:
            after = json.load(f)
        self.assertEqual(after["model"], "x")
        self.assertEqual(after["hooks"]["Stop"][0]["hooks"][0]["command"], "other-tool")
        self.assertEqual(stat.S_IMODE(os.stat(self.path).st_mode), 0o600, "it may hold keys: not made readable")

    def test_nothing_reached_the_file_is_said_so_not_restored(self):
        cmd = "'%s/dcbot/tools/dchook' --global" % ENGINE
        new, _, added = applycore.new_settings(self.before, plancore.compare([self.hook_want("Stop", "", cmd, 20)], [], []))
        folder = os.path.dirname(self.path)
        os.chmod(folder, 0o555)
        try:
            if os.access(folder, os.W_OK):
                self.skipTest("root writes anyway")
            with self.assertRaises(applycore.Refused) as e:
                applycore.write_settings(self.raw, self.before, new, added, "b")
        finally:
            os.chmod(folder, 0o755)
        self.assertIn("沒寫進去", str(e.exception))
        self.assertNotIn("還原", str(e.exception))
        with open(self.path, "rb") as f:
            self.assertEqual(f.read(), self.raw)

    def test_written_wrong_is_put_back_and_read_back(self):
        cmd = "'%s/dcbot/tools/dchook' --global" % ENGINE
        new, _, added = applycore.new_settings(self.before, plancore.compare([self.hook_want("Stop", "", cmd, 20)], [], []))
        real = applycore.replace_keeping_mode
        with mock.patch.object(applycore, "replace_keeping_mode",
                               lambda p, d, m: real(p, d.replace(b"dchook", b"dchoak") if d != self.raw else d, m)):
            with self.assertRaises(applycore.Refused) as e:
                applycore.write_settings(self.raw, self.before, new, added, "b")
        self.assertIn("重讀確認過", str(e.exception))
        with open(self.path, "rb") as f:
            self.assertEqual(f.read(), self.raw)

    def test_a_declared_setting_is_raised_to_its_floor_and_nothing_else_changes(self):
        rows = [("core", "/e", {"settings": [{"file": "~/.claude/settings.json", "key": "cleanupPeriodDays", "at_least": 36500}]}, "on", "")]
        items, odd = plancore.settings_wanted(rows)
        self.assertEqual((items, odd), ([("core", "cleanupPeriodDays", 36500)], []))
        for now, short in ((None, True), (30, True), (36500, False), (99999, False)):
            cc = dict(self.before, **({"cleanupPeriodDays": now} if now is not None else {}))
            self.assertEqual(bool(plancore.settings_short(cc, items)), short, now)
        new, actions, raised = applycore.raise_settings(self.before, plancore.settings_short(self.before, items))
        self.assertEqual((raised, new["cleanupPeriodDays"]), (["cleanupPeriodDays"], 36500))
        self.assertIn("沒有 → 36500", actions[0])
        self.assertTrue(applycore.write_settings(self.raw, self.before, new, [], "b", raised))
        with open(self.path) as f:
            after = json.load(f)
        self.assertEqual(after, dict(self.before, cleanupPeriodDays=36500))

    def test_a_setting_is_never_lowered_nor_another_key_changed_under_its_name(self):
        before = dict(self.before, cleanupPeriodDays=99999)
        with self.assertRaises(applycore.Refused):
            applycore.write_settings(self.raw, before, dict(before, cleanupPeriodDays=36500), [], "b", ["cleanupPeriodDays"])
        with self.assertRaises(applycore.Refused):
            applycore.write_settings(self.raw, self.before, dict(self.before, cleanupPeriodDays=36500, model="y"), [], "b",
                                     ["cleanupPeriodDays"])
        with open(self.path, "rb") as f:
            self.assertEqual(f.read(), self.raw)

    def test_a_setting_that_is_not_a_number_or_in_another_file_is_left_alone(self):
        rows = [("core", "/e", {"settings": [{"file": "~/.claude/settings.json", "key": "cleanupPeriodDays", "at_least": 36500},
                                             {"file": "~/.other.json", "key": "x", "at_least": 1},
                                             {"file": "~/.claude/settings.json", "key": "y", "at_least": "1"}]}, "on", ""),
                ("off-one", "/o", {"settings": [{"file": "~/.claude/settings.json", "key": "z", "at_least": 1}]}, "off", "")]
        items, odd = plancore.settings_wanted(rows)
        self.assertEqual(items, [("core", "cleanupPeriodDays", 36500)])
        self.assertEqual([w for w, _ in odd], ["core/x", "core/y"])
        cc = {"cleanupPeriodDays": "forever"}
        self.assertEqual(plancore.settings_short(cc, items), [])
        self.assertEqual(plancore.settings_odd(cc, items), [("core", "cleanupPeriodDays")])

    def test_nothing_to_add_means_not_written_at_all(self):
        new, actions, added = applycore.new_settings(self.before, [])
        self.assertFalse(applycore.write_settings(self.raw, self.before, new, added, "b"))
        with open(self.path, "rb") as f:
            self.assertEqual(f.read(), self.raw, "not even reformatted")

    def test_a_hook_nothing_asks_for_is_never_removed(self):
        ours = dict(self.before, hooks={"Stop": [{"hooks": [{"type": "command", "command": "'%s/old/hook'" % ENGINE}]}]})
        new, actions, added = applycore.new_settings(ours, plancore.compare([], [], plancore.installed_hooks(ours, ENGINE)))
        self.assertEqual(new, ours)
        self.assertTrue(any("不拿掉 hook" in a for a in actions))

    def test_a_file_changed_since_it_was_read_is_not_written(self):
        cmd = "'%s/dcbot/tools/dchook' --global" % ENGINE
        new, _, added = applycore.new_settings(self.before, plancore.compare([self.hook_want("Stop", "", cmd, 20)], [], []))
        with open(self.path, "w") as f:
            f.write(json.dumps(dict(self.before, model="y")))
        with self.assertRaises(applycore.Refused):
            applycore.write_settings(self.raw, self.before, new, added, "b")
        with open(self.path) as f:
            self.assertEqual(json.load(f)["model"], "y")

    def test_restore_of_a_file_that_was_not_there_takes_our_hooks_out_again(self):
        os.remove(self.path)
        folder = applycore.make_backup(b"", None)
        cmd = "'%s/dcbot/tools/dchook' --global" % ENGINE
        with open(self.path, "w") as f:
            json.dump({"hooks": {"Stop": [{"hooks": [{"type": "command", "command": cmd}]}]}}, f)
        self.put([b"x"])
        done = applycore.restore(os.path.basename(folder), ENGINE)
        self.assertFalse(os.path.exists(self.path), "only our hook was in it: gone")
        self.assertTrue(any("原本不存在" in d for d in done))
        self.assertEqual(self.get(), [], "the crontab that was empty")



class HooksOff(Settings):
    """C-85 (a): a plugin turned off here (disabled_on) has its hooks taken out of settings.json, exactly as its
    plugin.json declares them (as SECURITY.md says: turned off, chatlog records nothing more); the person's own hooks,
    a hook like it with another timeout, and the hooks of the plugins on here stay."""
    def chatlog(self):
        with open(os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                               "chatlog", "plugin.json"), encoding="utf-8") as f:
            return json.load(f)

    def rows(self, where):
        return [("chatlog", ENGINE + "/chatlog", self.chatlog(), where, ""),
                ("other", ENGINE + "/other", {"hooks": [{"event": "Stop", "command": "{plugin}/o.sh"}]}, "on", "")]

    def installed(self, want):
        """settings.json with every hook of `want` (as apply wrote them), the person's own beside them."""
        d = copy.deepcopy(self.before)
        for w in want:
            entry = {"type": "command", "command": w["text"]}
            if w["key"][3] is not None:
                entry["timeout"] = w["key"][3]
            d.setdefault("hooks", {}).setdefault(w["key"][0], []).append({"hooks": [entry]})
        return d

    def test_off_here_its_hooks_go_the_rest_stays(self):
        on, _ = plancore.wanted(self.rows("on"), "host-a", {}, None)
        hooks = [w for w in on if w["kind"] == "hook"]
        self.assertEqual(sorted(w["plugin"] for w in hooks), ["chatlog", "chatlog", "other"])
        before = self.installed(hooks)
        mine = [w for w in hooks if w["plugin"] == "chatlog"][0]
        before["hooks"]["Stop"].append({"hooks": [{"type": "command", "command": mine["text"], "timeout": 99}]})  # another timeout
        raw = json.dumps(before).encode("utf-8")
        with open(self.path, "wb") as f:
            f.write(raw)
        for where in ("disabled", "off"):
            with self.subTest(where=where):
                want, _ = plancore.wanted(self.rows(where), "host-a", {}, None)
                result = plancore.compare(want, [], plancore.installed_hooks(before, ENGINE))
                self.assertEqual(sorted((r[0], r[1]["plugin"] if r[1] else None) for r in result if r[0] != "same"),
                                 [("hook off", "chatlog"), ("hook off", "chatlog"), ("unclaimed", None)])
        new, actions, added = applycore.new_settings(before, result)
        self.assertTrue(applycore.write_settings(raw, before, new, added, "b"))
        with open(self.path) as f:
            after = json.load(f)
        left = [(e, h["command"], h.get("timeout")) for e, gs in after["hooks"].items() for g in gs for h in g["hooks"]]
        self.assertEqual(sorted(left), sorted([("Stop", "other-tool", 5), ("Stop", ENGINE + "/other/o.sh", None),
                                               ("Stop", mine["text"], 99)]))
        self.assertNotIn("SessionEnd", after["hooks"], "an event left empty goes")
        self.assertEqual(sum("chatlog 在這台沒在用" in a for a in actions), 2)
        want, _ = plancore.wanted(self.rows("on"), "host-a", {}, None)  # turned on again: added back
        result = plancore.compare(want, [], plancore.installed_hooks(after, ENGINE))
        self.assertEqual(sorted(r[1]["name"] for r in result if r[0] == "missing" and r[1]["kind"] == "hook"), ["SessionEnd", "Stop"])

    def test_a_plugin_on_here_or_unknown_takes_nothing(self):
        for where in ("on", "unknown"):
            want, _ = plancore.wanted(self.rows(where), "host-a", {}, None)
            self.assertFalse([w for w in want if w["kind"] == "hook off"], where)

    def test_the_report_names_them(self):
        self.assertEqual(plugins.LABEL["hook off"], "插件在這台關掉了")

class Elsewhere(Rig):
    """A line whose item's own "on" plainly leaves this machine out (the bot's supervisor lines once the bot has moved)
    is removed; when the settings, the bot's machine or the machine list cannot be told, nothing is (manager
    2026-10-06)."""
    RUN = 'cd "{engine}/dcbot" && /usr/bin/python3 supervisor.py >> state/supervisor.out 2>&1'
    CORE = {"cron": [{"name": "supervisor-boot", "on": "bot", "when": "@reboot", "run": RUN},
                     {"name": "supervisor", "on": "bot", "when": "*/5 * * * *", "run": RUN}]}

    def rows(self):
        return [("core", ENGINE, self.CORE, "on", "")]

    def lines(self):
        cmd = self.RUN.replace("{engine}", ENGINE)
        return [b"SHELL=/bin/bash", ("@reboot %s  # altention:core" % cmd).encode("utf-8"),
                ("*/5 * * * * %s  # altention:core" % cmd).encode("utf-8"), OTHERS[4]]

    def away(self, settings={}, bot="host-b", known=("host-a", "host-b")):
        with mock.patch.object(plancore.plugin, "expand", lambda t, f=None, s=None: t.replace("{engine}", ENGINE)):
            return plancore.elsewhere(self.rows(), "host-a", settings, bot, set(known))

    def test_the_bots_lines_go_from_a_machine_that_is_not_the_bots(self):
        away = self.away()
        self.assertEqual([w["name"] for w in away], ["supervisor-boot", "supervisor"])
        wrote, actions = self.apply_once(self.lines(), rows=self.rows(), away=away)
        self.assertTrue(wrote)
        self.assertEqual(self.get(), [b"SHELL=/bin/bash", OTHERS[4]])
        self.assertTrue(all("不在這台跑" in a for a in actions), actions)
        self.assertIn("core/supervisor-boot", actions[0])
        self.assertIn("core/supervisor ", actions[1], "each line under its own item, not both as the first")

    def test_nothing_goes_when_anything_cannot_be_told(self):
        for kw in ({"settings": None}, {"bot": None}, {"known": ()}):
            with self.subTest(**{k: str(v) for k, v in kw.items()}):
                self.assertEqual(self.away(**kw), [])
                wrote, actions = self.apply_once(self.lines(), rows=self.rows(), away=self.away(**kw))
                self.assertFalse(wrote)
                self.assertEqual(self.get(), self.lines())

    def test_a_machine_not_on_the_list_or_an_on_of_sources_is_never_elsewhere(self):
        with mock.patch.object(plancore.plugin, "expand", lambda t, f=None, s=None: t.replace("{engine}", ENGINE)):
            self.assertEqual(plancore.elsewhere(self.rows(), "host-a-lab", {}, "host-b", {"host-a", "host-b"}), [])
            core = {"cron": [dict(self.CORE["cron"][1], on="sources"), dict(self.CORE["cron"][1], on="one")]}
            self.assertEqual(plancore.elsewhere([("core", ENGINE, core, "on", "")], "host-a", {}, "host-b",
                                                {"host-a", "host-b"}), [])

    def test_on_the_bots_own_machine_they_stay(self):
        self.assertEqual(self.away(bot="host-a"), [])

    def test_an_unknown_word_in_on_is_not_taken_for_elsewhere(self):
        core = {"cron": [dict(self.CORE["cron"][1], on="bott")]}
        with mock.patch.object(plancore.plugin, "expand", lambda t, f=None, s=None: t.replace("{engine}", ENGINE)):
            self.assertEqual(plancore.elsewhere([("core", ENGINE, core, "on", "")], "host-a", {}, "host-b",
                                                {"host-a", "host-b"}), [])

    def test_an_old_format_line_is_never_elsewhere(self):
        cmd = self.RUN.replace("{engine}", ENGINE)
        lines = [("*/5 * * * * %s" % cmd).encode("utf-8")]
        wrote, actions = self.apply_once(lines, rows=self.rows(), away=self.away())
        self.assertFalse(wrote)
        self.assertEqual(self.get(), lines)


class ScheduleFiles(Rig):
    def test_made_only_in_an_existing_folder_and_only_when_never_noted(self):
        have = os.path.join(self.tmp, "有的.json")
        with open(have, "w") as f:
            f.write('[{"name": "x"}]')
        fresh = os.path.join(self.tmp, "沒有的.json")
        nofolder = os.path.join(self.tmp, "no such dir", "x.json")
        noted = os.path.join(self.tmp, "記得的.json")
        rows = [("planner", "/p", {"schedule_files": "{setting:planner.files}"}, "on", ""),
                ("progress", "/q", {"schedule_files": [os.path.join(self.tmp, "off.json")]}, "off", "")]
        files = applycore.schedule_files(rows, {"planner": {"files": [have, fresh, nofolder, noted]}})
        self.assertEqual([p for _, p in files], [have, fresh, nofolder, noted])
        from altention import scheduler
        with mock.patch.object(scheduler, "read_state", lambda m, dry=False: {} if dry else {os.path.realpath(noted) + "#a": {}}):
            said = applycore.create_missing(files, "host-a")
        self.assertTrue(os.path.exists(fresh))
        self.assertFalse(os.path.exists(os.path.dirname(nofolder)), "no folders are made")
        self.assertFalse(os.path.exists(noted), "had notes and is gone: said, not covered up")
        self.assertTrue(any("記得它的項目" in s for s in said))
        with open(have) as f:
            self.assertEqual(f.read(), '[{"name": "x"}]')


class Unlisted(Rig):
    """This machine's name not on the machine list that was read: plan says unknown and exits non-zero, apply --write
    writes nothing (code-review 2026-10-06: the single-machine plugins' lines were taken for off and removed)."""

    def with_config(self, fn):
        engine = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        inst = os.path.join(self.tmp, "instance")
        os.makedirs(inst, exist_ok=True)
        cfg = plugins.config
        with mock.patch.object(cfg, "instance_dir", return_value=inst), \
                mock.patch.object(cfg, "settings", return_value={"plugins": {"progress": {"on": "host-a"}, "usage": {"on": "host-a"}}}), \
                mock.patch.object(cfg, "setting", return_value=None), \
                mock.patch.object(cfg, "inventory", return_value={"machines": {"host-a": {}, "host-b": {}}}), \
                mock.patch.object(cfg, "this_machine", return_value="host-a-lab"), \
                mock.patch.object(cfg, "bot_machine", return_value=("host-a", {}, False)), \
                mock.patch.object(plugins.plugin, "ENGINE", engine):
            out = io.StringIO()
            return fn(out), out.getvalue(), engine

    def test_plan_unknown_and_apply_writes_nothing(self):
        engine = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        progress = ('*/10 * * * * /usr/bin/python3 "%s/progress/progress.py" run  # altention:progress' % os.path.realpath(engine)).encode("utf-8")
        self.put([b"SHELL=/bin/bash", progress])
        code, text, _ = self.with_config(lambda out: plugins.plan(out))
        self.assertNotEqual(code, 0, text)
        self.assertIn("不在機器清單上", text)
        self.assertIn("內容：不知道（這台不在機器清單上", text)
        settings = os.path.expanduser("~/.claude/settings.json")
        self.assertFalse(os.path.exists(settings))
        code, text, _ = self.with_config(lambda out: plugins.apply(True, (), out))
        self.assertEqual(code, 1, text)
        self.assertNotIn("拿掉", text)
        self.assertEqual(self.get(), [b"SHELL=/bin/bash", progress], text)
        self.assertFalse(os.path.exists(settings), "not a hook added either")


class Report(Rig):
    """3a.12, report only: `plugins.py report` says what `apply --write` would change and writes nothing: not the
    crontab, not settings.json, no backup, no cleaning (manager 2026-10-09)."""

    def report(self, machine="host-a", settings=None, inventory=None, bad=None):
        engine = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        self.engine = os.path.realpath(engine)
        inst = os.path.join(self.tmp, "instance")
        os.makedirs(inst, exist_ok=True)
        cfg = plugins.config
        settings = {"plugins": {"progress": {"on": "host-a"}, "usage": {"on": "host-a"}}} if settings is None else settings
        inventory = {"machines": {"host-a": {"roles": ["chatlog", "progress", "usage", "planner"]}, "host-b": {}}} if inventory is None else inventory

        def never(*a, **k):
            raise AssertionError("the report called something that writes")
        writes = [mock.patch.object(applycore, n, never) for n in (
            "write_crontab", "install_crontab", "write_settings", "make_backup", "clean_if_free", "clean_leftovers",
            "create_missing", "restore", "new_crontab", "new_settings", "raise_settings")]
        with mock.patch.object(cfg, "instance_dir", return_value=inst), \
                mock.patch.object(cfg, "settings", **({"side_effect": bad} if bad else {"return_value": settings})), \
                mock.patch.object(cfg, "setting", return_value=None), \
                mock.patch.object(cfg, "inventory", return_value=inventory), \
                mock.patch.object(cfg, "this_machine", return_value=machine), \
                mock.patch.object(cfg, "bot_machine", return_value=("host-a", {}, False)), \
                mock.patch.object(plugins.plugin, "ENGINE", engine):
            for w in writes:
                w.start()
            try:
                return plugins.report()
            finally:
                for w in writes:
                    w.stop()

    def all_as_asked(self):
        """A crontab and a settings.json with everything this engine's plugins ask of host-a, by apply itself."""
        self.put([b"SHELL=/bin/bash", b"0 3 * * * /opt/someone/else.sh"])
        out = io.StringIO()
        engine = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        self.engine = os.path.realpath(engine)
        cfg = plugins.config
        with mock.patch.object(cfg, "instance_dir", return_value=os.path.join(self.tmp, "instance")), \
                mock.patch.object(cfg, "settings", return_value={"plugins": {"progress": {"on": "host-a"}, "usage": {"on": "host-a"}}}), \
                mock.patch.object(cfg, "setting", return_value=None), \
                mock.patch.object(cfg, "inventory", return_value={"machines": {"host-a": {"roles": ["chatlog", "progress", "usage", "planner"]}, "host-b": {}}}), \
                mock.patch.object(cfg, "this_machine", return_value="host-a"), \
                mock.patch.object(cfg, "bot_machine", return_value=("host-a", {}, False)), \
                mock.patch.object(plugins.plugin, "ENGINE", engine):
            os.makedirs(os.path.join(self.tmp, "instance"), exist_ok=True)
            self.assertEqual(plugins.apply(True, (), out), 0, out.getvalue())

    def snapshot(self):
        settings = os.path.expanduser("~/.claude/settings.json")
        with open(self.file, "rb") as f:
            cron = f.read()
        with open(settings, "rb") as f:
            cc = f.read()
        backups = sorted(os.listdir(applycore.backup_root())) if os.path.isdir(applycore.backup_root()) else []
        return cron, os.stat(self.file).st_mtime_ns, cc, os.stat(settings).st_mtime_ns, backups

    def test_everything_as_asked_is_a_report_with_nothing_in_it_and_not_a_byte_written(self):
        self.all_as_asked()
        before = self.snapshot()
        rep = self.report()
        self.assertEqual(self.snapshot(), before)
        self.assertEqual((rep["add"], rep["remove"], rep["retime"], rep["core"], rep["refuse"]), ([], [], [], [], None), rep)
        self.assertEqual(rep["machine"], "host-a")
        self.assertGreater(rep["marked_lines"], 3)
        self.assertEqual(rep["unclaimed"], 0, "someone else's line is not ours to count")
        json.dumps(rep)

    def test_a_line_and_a_hook_gone_are_what_apply_would_add_and_nothing_is_added(self):
        self.all_as_asked()
        lines = [l for l in self.get() if b"progress/progress.py" not in l]
        self.put(lines)
        settings = os.path.expanduser("~/.claude/settings.json")
        with open(settings, encoding="utf-8") as f:
            cc = json.load(f)
        cc["hooks"]["Stop"] = [g for g in cc["hooks"]["Stop"] if "chatlog.py" not in json.dumps(g)]
        with open(settings, "w", encoding="utf-8") as f:
            json.dump(cc, f)
        before = self.snapshot()
        rep = self.report()
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(sorted((i["plugin"], i["name"], i["kind"]) for i in rep["add"]), [("chatlog", "Stop", "hook"), ("progress", "run", "cron")])
        self.assertIsNone(rep["refuse"])

    def test_a_core_line_gone_is_the_cores_and_never_among_what_apply_would_add(self):
        self.all_as_asked()
        self.put([l for l in self.get() if b"cron_sync.py" not in l])
        rep = self.report()
        self.assertEqual([(i["plugin"], i["name"], i["status"]) for i in rep["core"]], [("core", "sync", "missing")])
        self.assertEqual(rep["add"], [])

    def test_a_time_changed_by_hand_is_said_apart(self):
        self.all_as_asked()
        self.put([l.replace(b"*/10 * * * *", b"*/20 * * * *") if b"progress/progress.py" in l else l for l in self.get()])
        rep = self.report()
        self.assertEqual([(i["plugin"], i["name"]) for i in rep["retime"]], [("progress", "run")])
        self.assertEqual(rep["add"], [])

    def test_one_line_of_ours_no_plugin_asks_for_is_a_removal_and_two_are_refused(self):
        """Manager 2026-10-09: two or more, or over a third of the marked lines: never to be written out."""
        self.all_as_asked()
        extra = ('5 5 * * * /usr/bin/python3 "%s/progress/old.py"  # altention:progress' % self.engine).encode("utf-8")
        self.put(self.get() + [extra])
        rep = self.report()
        self.assertEqual((len(rep["remove"]), len(rep["kept"])), (0, 1), "progress is on: apply keeps it, said so")
        self.assertIsNone(rep["refuse"])
        self.put(self.get() + [extra.replace(b"old.py", b"older.py")])
        before = self.snapshot()
        rep = self.report()
        self.assertEqual(len(rep["kept"]), 2)
        self.assertIn("mass-removal", rep["refuse"], "the guard still counts them")
        self.assertEqual(self.snapshot(), before)

    def test_over_a_third_of_the_marked_lines_is_refused_even_when_it_is_one(self):
        self.put([b"SHELL=/bin/bash", ('5 5 * * * /usr/bin/python3 "%s/progress/old.py"  # altention:progress' % os.path.realpath(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))).encode("utf-8")])
        with open(os.path.expanduser("~/.claude/settings.json"), "w") as f:
            f.write("{}")
        os.makedirs(os.path.dirname(os.path.expanduser("~/.claude/settings.json")), exist_ok=True)
        rep = self.report()
        self.assertEqual(len(rep["remove"]) + len(rep["kept"]), 1)
        self.assertIn("mass-removal", rep["refuse"])

    def test_not_knowing_what_this_machine_is_is_refused(self):
        self.all_as_asked()
        for what in ({"machine": "host-a-lab"}, {"inventory": {"machines": {}}}, {"bad": ValueError("broken")}):
            rep = self.report(**what)
            self.assertIn("mass-removal", rep["refuse"] or "", what)
        os.environ["FAKE_FAIL"] = "1"
        try:
            rep = self.report()
        finally:
            del os.environ["FAKE_FAIL"]
        self.assertIn("讀不出來", rep["refuse"])
        self.assertEqual(rep["add"] + rep["remove"], [], "what could not be read is not compared")

    def test_no_machine_list_at_all_is_the_one_machine_install(self):
        """support-4 dry run #1: no machines.json is a single machine (config.machines()), not a machine that does not
        know what it is; a list without this machine, or an empty one, is still refused (above)."""
        self.all_as_asked()
        rep = self.report(inventory={})
        self.assertIsNone(rep["refuse"], rep)

    def test_a_plugin_turned_off_here_on_purpose_is_said_and_nothing_is_added_for_it(self):
        self.all_as_asked()
        rep = self.report(settings={"plugins": {"progress": {"on": "host-a"}, "usage": {"on": "host-a", "disabled_on": ["host-a"]},
                                                "chatlog": {"disabled_on": ["host-a"]}}})
        self.assertIn("chatlog", rep["disabled"])
        self.assertFalse([i for i in rep["add"] if i["plugin"] == "chatlog"])

    def test_the_report_says_nothing_of_roles(self):
        self.all_as_asked()
        rep = self.report(inventory={"machines": {"host-a": {"roles": ["chatlog", "usage", "planner"]}, "host-b": {}}})
        self.assertNotIn("roles_disagree", rep)

    def test_the_command_prints_the_report_as_json(self):
        self.all_as_asked()
        out = io.StringIO()
        with mock.patch.object(plugins, "report", return_value={"format": 1, "add": []}), mock.patch.object(sys, "stdout", out):
            self.assertEqual(plugins.main(["report"]), 0)
        self.assertEqual(json.loads(out.getvalue()), {"format": 1, "add": []})


class Hold(Rig):
    def test_hold_bot_holds_back_only_the_items_whose_on_is_bot(self):
        """--hold bot (ops/apply.py while another machine still runs the bot): the supervisor lines wait, the core's
        hooks every machine needs do not (code-review 2026-10-06)."""
        cmd = 'cd "%s/dcbot" && /usr/bin/python3 supervisor.py' % ENGINE
        sup = dict(want("core", "supervisor", "*/5 * * * *", cmd), on="bot")
        other = dict(want("core", "other", "0 1 * * *", "/usr/bin/true"), on="all")
        hook = {"plugin": "core", "name": "Stop", "kind": "hook", "text": "dchook --global",
                "key": ("Stop", "", ("dchook", "--global"), 20)}
        self.put([b"SHELL=/bin/bash"])
        raw = plancore.read_crontab_raw()
        settings_path = os.path.expanduser("~/.claude/settings.json")
        os.makedirs(os.path.dirname(settings_path), exist_ok=True)
        with open(settings_path, "w") as f:
            f.write("{}")
        rows = [("core", ENGINE, {}, "on", "")]
        got = ("host-a", rows, [], [sup, other, hook], [], raw, [], [], b"{}", {}, [], ENGINE, [])
        out = io.StringIO()
        with mock.patch.object(plugins, "gather", return_value=got), \
                mock.patch.object(plugins.config, "settings", return_value={}), \
                mock.patch.object(plugins, "unlisted", return_value=""):
            plugins.apply(False, (), out, hold=["bot"])
        text = out.getvalue()
        self.assertIn("先不動（--hold）：core/supervisor", text)
        self.assertIn("加上：core/other", text)
        self.assertIn("加上 hook：core/Stop", text)
        os.remove(settings_path)

    def test_a_held_plugins_line_is_not_even_marked(self):
        """--hold leaves its lines byte for byte: an old-format line is not given the new marker either
        (#code-review-2 2026-10-06)."""
        cmd = '/usr/bin/python3 "%s/progress/progress.py" run' % ENGINE
        line = ("*/10 * * * * %s" % cmd).encode("utf-8")
        self.put([b"SHELL=/bin/bash", line])
        raw = plancore.read_crontab_raw()
        texts = [t for t in (plancore.text(l) for l in plancore.raw_lines(raw)) if t is not None]
        have, paused, _ = plancore.installed_cron(texts, ENGINE)
        w = want("progress", "run", "*/10 * * * *", cmd)
        settings_path = os.path.expanduser("~/.claude/settings.json")
        os.makedirs(os.path.dirname(settings_path), exist_ok=True)
        with open(settings_path, "w") as f:
            f.write("{}")
        got = ("host-a", [("progress", ENGINE, {}, "on", "")], [], [w], [], raw, have, paused, b"{}", {}, [], ENGINE, [])
        out = io.StringIO()
        with mock.patch.object(plugins, "gather", return_value=got), mock.patch.object(plugins.config, "settings", return_value={}), \
                mock.patch.object(plugins, "unlisted", return_value=""):
            plugins.apply(True, (), out, hold=["progress"])
        self.assertIn("先不動（--hold）：progress/run", out.getvalue())
        self.assertNotIn("標記", out.getvalue())
        self.assertEqual(self.get(), [b"SHELL=/bin/bash", line])
        os.remove(settings_path)


class Added(Rig):
    """apply notes when it added each plugin's cron line, for doctor to tell "not its turn yet" from "never runs"
    (owner's install 2026-10-15)."""

    def apply(self):
        raw = plancore.read_crontab_raw()
        texts = [t for t in (plancore.text(l) for l in plancore.raw_lines(raw)) if t is not None]
        have, paused, _ = plancore.installed_cron(texts, ENGINE)
        w = want("progress", "run", "*/10 * * * *", '/usr/bin/python3 "%s/progress/progress.py" run' % ENGINE)
        settings_path = os.path.expanduser("~/.claude/settings.json")
        os.makedirs(os.path.dirname(settings_path), exist_ok=True)
        with open(settings_path, "w") as f:
            f.write("{}")
        got = ("host-a", [("progress", ENGINE, {}, "on", "")], [], [w], [], raw, have, paused, b"{}", {}, [], ENGINE, [])
        out = io.StringIO()
        with mock.patch.object(plugins, "gather", return_value=got), mock.patch.object(plugins.config, "settings", return_value={}), \
                mock.patch.object(plugins, "unlisted", return_value=""):
            self.assertEqual(plugins.apply(True, (), out), 0, out.getvalue())
        return os.path.join(applycore.state_root(), "added.json")

    def read(self, path):
        with open(path) as f:
            return json.load(f)

    def test_a_line_added_is_noted_and_a_later_apply_with_nothing_to_add_keeps_the_time(self):
        self.put([b"SHELL=/bin/bash"])
        with mock.patch.object(applycore.time, "time", return_value=1000.0):
            path = self.apply()
        self.assertEqual(self.read(path), {"progress": 1000.0})
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
        with mock.patch.object(applycore.time, "time", return_value=5000.0):
            self.apply()
        self.assertEqual(self.read(path), {"progress": 1000.0}, "nothing added the second time")

    def test_a_broken_note_is_started_again_and_never_stops_apply(self):
        os.makedirs(applycore.state_root(), exist_ok=True)
        path = os.path.join(applycore.state_root(), "added.json")
        with open(path, "w") as f:
            f.write("[1, 2")
        applycore.note_added({"chatlog"}, now=7.0)
        self.assertEqual(self.read(path), {"chatlog": 7.0})
        with mock.patch.object(applycore, "private_write", side_effect=OSError("disk full")):
            applycore.note_added({"core"}, now=8.0)
        self.assertEqual(self.read(path), {"chatlog": 7.0})
        self.assertEqual([f for f in os.listdir(applycore.state_root()) if ".tmp-" in f], [])


class Command(Rig):
    def run_cli(self, *argv):
        out = io.StringIO()
        with mock.patch("sys.stdout", out):
            code = plugins.main(["apply"] + list(argv))
        return code, out.getvalue()

    def test_a_mistyped_flag_writes_nothing(self):
        for argv in (["--dry_run"], ["-n"], ["--allow-time"], ["--write", "--force"]):
            with mock.patch.object(plugins, "apply") as apply:
                code, text = self.run_cli(*argv)
            self.assertEqual(code, 2, argv)
            apply.assert_not_called()

    def test_without_write_it_only_shows(self):
        with mock.patch.object(plugins, "apply", return_value=0) as apply:
            self.run_cli()
            self.run_cli("--allow-time", "chatlog/sync")
            self.run_cli("--write")
        self.assertEqual([c.args for c in apply.call_args_list], [(False, []), (False, ["chatlog/sync"]), (True, [])])


class Removable(unittest.TestCase):
    def test_off_disabled_and_drafts_only(self):
        """C-61 E1: a draft (an external plugin the instance does not declare) asks for nothing, and its marked lines
        go like an off plugin's; unknown and a plugin.json that could not be read keep theirs."""
        rows = [(n, "/f/" + n, {} if w != "broken" else None, w if w != "broken" else "unknown", "")
                for n, w in (("a", "on"), ("b", "off"), ("c", "disabled"), ("d", "draft"), ("e", "unknown"), ("f", "broken"))]
        self.assertEqual(applycore.removable(rows), {"b", "c", "d"})


if __name__ == "__main__":
    unittest.main()


class Overrides(Report):
    """C-37: the instance's plugins.<name>.schedules overrides a plugin's when or turns one item off (and core.schedules
    the core's watch). apply --write changes only the lines of the items overridden, their time only, without
    --allow-time; an override that cannot be used leaves its line byte for byte; taking an override away puts the
    plugin's time back; the report does not take such changes for a mass removal (manager 2026-10-17)."""
    BASE = {"plugins": {"progress": {"on": "host-a"}, "usage": {"on": "host-a"}}}

    def apply_with(self, settings, write=True):
        out = io.StringIO()
        engine = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        cfg = plugins.config
        with mock.patch.object(cfg, "instance_dir", return_value=os.path.join(self.tmp, "instance")), \
                mock.patch.object(cfg, "settings", return_value=settings), \
                mock.patch.object(cfg, "setting", return_value=None), \
                mock.patch.object(cfg, "inventory", return_value={"machines": {"host-a": {"roles": ["chatlog", "progress", "usage", "planner"]}, "host-b": {}}}), \
                mock.patch.object(cfg, "this_machine", return_value="host-a"), \
                mock.patch.object(cfg, "bot_machine", return_value=("host-a", {}, False)), \
                mock.patch.object(plugins.plugin, "ENGINE", engine):
            code = plugins.apply(write, (), out)
        return code, out.getvalue()

    def settings(self, **schedules):
        s = json.loads(json.dumps(self.BASE))
        for name, o in schedules.items():
            if name == "core":
                s["core"] = {"schedules": o}
            else:
                s["plugins"].setdefault(name, {})["schedules"] = o
        return s

    def line(self, marker):
        found = [l for l in self.get() if l.endswith(("# altention:%s" % marker).encode("utf-8"))]
        self.assertEqual(len(found), 1, (marker, self.get()))
        return found[0]

    def test_chatlog_turned_off_here_its_hooks_and_line_go_and_come_back(self):
        """C-85 (a): disabled_on this machine, apply takes chatlog's hooks out of settings.json (and its cron line,
        as before); the other hooks stay; turned on again, both come back."""
        self.all_as_asked()
        path = os.path.expanduser("~/.claude/settings.json")
        def hooks():
            with open(path) as f:
                d = json.load(f)
            return sorted(h["command"] for gs in (d.get("hooks") or {}).values() for g in gs for h in g["hooks"])
        before = hooks()
        theirs = [c for c in before if "/chatlog/chatlog.py" in c]
        self.assertEqual(len(theirs), 2, before)
        off = self.settings()
        off["plugins"]["chatlog"] = {"disabled_on": ["host-a"]}
        code, out = self.apply_with(off)
        self.assertEqual(code, 0, out)
        self.assertEqual(hooks(), [c for c in before if c not in theirs])
        self.assertFalse([l for l in self.get() if l.endswith(b"# altention:chatlog/sync")])
        self.assertIn("chatlog 在這台沒在用", out)
        code, out = self.apply_with(self.settings())
        self.assertEqual(code, 0, out)
        self.assertEqual(hooks(), before)
        self.line("chatlog/sync")

    def test_three_plugins_retimed_at_once_only_their_lines_and_only_the_time(self):
        self.all_as_asked()
        before = self.get()
        s = self.settings(progress={"run": {"when": {"at": "03:00", "days": "daily"}}},
                          usage={"collect": {"when": {"every": "3h"}}},
                          chatlog={"sync": {"when": {"every": "1h", "offset": "machine"}}})
        rep = self.report(settings=s)
        self.assertIsNone(rep["refuse"], rep)
        self.assertEqual(sorted((r["plugin"], r["name"], r.get("auto")) for r in rep["retime"]),
                         [("chatlog", "sync", True), ("progress", "run", True), ("usage", "collect", True)])
        self.assertEqual(rep["remove"] + rep["add"], [])
        code, text = self.apply_with(s)
        self.assertEqual(code, 0, text)
        self.assertEqual(text.count("改時間（實例設定改的）"), 3, text)
        after = self.get()
        changed = [(b, a) for b, a in zip(before, after) if b != a]
        self.assertEqual(len(before), len(after))
        self.assertEqual(len(changed), 3, changed)
        for b, a in changed:
            self.assertEqual(b.split()[5:], a.split()[5:], "the command and the marker stay")
        self.assertTrue(self.line("progress/run").startswith(b"0 3 * * * "))
        self.assertTrue(self.line("usage/collect").startswith(b"0 */3 * * * "))
        self.assertEqual(self.line("chatlog/sync").split()[1:5], [b"*", b"*", b"*", b"*"])
        code, text = self.apply_with(s)
        self.assertIn("沒有要改的", text, "the next run finds nothing")

    def test_an_override_that_cannot_be_used_leaves_its_line_byte_for_byte(self):
        self.all_as_asked()
        before = self.get()
        for bad in ({"run": {"when": {"every": "7m"}}}, {"run": {"wehn": {"every": "1h"}}}, {"run": {"when": {"at": "3:00"}}},
                    {"run": {"enabled": "no"}}, {"run": "daily"}):
            s = self.settings(progress=bad)
            code, text = self.apply_with(s)
            self.assertEqual(self.get(), before, (bad, text))
            self.assertIn("不動 progress/run：不知道（實例的設定不能用：plugins.progress.schedules.run：", text)
            rep = self.report(settings=s)
            self.assertEqual((rep["retime"], rep["remove"], rep["add"], rep["kept"]), ([], [], [], []), rep)
            self.assertIn("progress/run", [u[0] for u in rep["unknown"]])
        s = self.BASE.copy()
        s = dict(s, plugins=dict(s["plugins"], progress={"on": "host-a", "schedules": ["run"]}))
        self.apply_with(s)
        self.assertEqual(self.get(), before, "a schedules block that is not an object: every item held")

    def test_an_item_turned_off_loses_its_line_and_comes_back_when_turned_on(self):
        self.all_as_asked()
        before = self.get()
        s = self.settings(usage={"collect": {"enabled": False}})
        rep = self.report(settings=s)
        self.assertIsNone(rep["refuse"], "the instance's own word is no mass removal")
        self.assertEqual([(r["status"], r["name"]) for r in rep["remove"]], [("turned off", "collect")])
        code, text = self.apply_with(s)
        self.assertIn("拿掉（實例設定關掉了 usage/collect）", text)
        self.assertEqual([l for l in before if b"altention:usage/collect" not in l], self.get())
        self.apply_with(self.settings())
        self.assertEqual(sorted(self.get()), sorted(before), "turned on again: the line is back")

    def test_an_override_taken_away_puts_the_plugins_time_back_a_hand_edit_is_left(self):
        self.all_as_asked()
        before = self.line("progress/run")
        self.apply_with(self.settings(progress={"run": {"when": {"every": "1h"}}}))
        self.assertTrue(self.line("progress/run").startswith(b"0 * * * * "))
        code, text = self.apply_with(self.settings())
        self.assertIn("改時間（實例的設定拿掉了，回到插件的時間）：progress/run", text)
        self.assertEqual(self.line("progress/run"), before)
        hand = before.replace(b"*/10 * * * *", b"*/20 * * * *", 1)  # changed by hand in the crontab: not ours to undo
        self.assertNotEqual(hand, before)
        self.put([hand if l == before else l for l in self.get()])
        code, text = self.apply_with(self.settings())
        self.assertIn("沒改（時間不同，要改請加 --allow-time progress/run）", text)
        self.assertEqual(self.line("progress/run"), hand)

    def test_the_report_says_auto_as_apply_does_it_when_an_override_is_taken_away(self):
        """#code-review-2 on c45394d: the report did not set apply's machine, read no overrides.json and said auto
        false for a line apply then put back by itself."""
        self.all_as_asked()
        self.apply_with(self.settings(progress={"run": {"when": {"every": "1h"}}}))
        applycore.MACHINE[0] = "this-machine"  # as a fresh `plugins.py report` process starts
        rep = self.report(settings=self.settings())
        self.assertEqual([(r["name"], r.get("auto")) for r in rep["retime"]], [("run", True)])
        applycore.MACHINE[0] = "this-machine"
        code, text = self.apply_with(self.settings())
        self.assertIn("改時間（實例的設定拿掉了，回到插件的時間）：progress/run", text)
        hand = [l for l in self.get() if l.endswith(b"# altention:chatlog/sync")][0]
        self.put([hand.replace(hand.split(b" ", 1)[0], b"59", 1) if l == hand else l for l in self.get()])
        applycore.MACHINE[0] = "this-machine"
        rep = self.report(settings=self.settings())
        self.assertEqual([(r["name"], r.get("auto")) for r in rep["retime"]], [("sync", None)], "a hand edit: not automatic")
        code, text = self.apply_with(self.settings())
        self.assertIn("沒改（時間不同，要改請加 --allow-time chatlog/sync）", text)

    def test_another_engines_line_with_the_same_marker_is_left(self):
        """#code-review on c45394d: turning remind off took another engine's line with the marker planner/remind too.
        Only a line naming this engine (or a folder it moved from) is turned off or held by its marker."""
        self.all_as_asked()
        other = b'*/5 * * * * /usr/bin/python3 "/srv/other/engine/planner/remind.py" >>"/srv/other/planner.out" 2>&1  # altention:planner/remind'
        self.put(self.get() + [other])
        self.apply_with(self.settings())
        before = self.get()
        self.assertIn(other, before, "as before: not ours to take")
        code, text = self.apply_with(self.settings(planner={"remind": {"enabled": False}}))
        self.assertEqual(text.count("拿掉（實例設定關掉了 planner/remind）"), 1, text)
        self.assertEqual(self.get(), [l for l in before if not (l.endswith(b"# altention:planner/remind") and l != other)])
        rep = self.report(settings=self.settings(planner={"remind": {"enabled": False}}))
        self.assertEqual([r["status"] for r in rep["remove"]], [], "this engine's line is gone already; the other is no item's")
        self.apply_with(self.settings(planner={"remind": {"when": {"every": "7m"}}}))
        self.assertIn(other, self.get(), "held: its line, and only this engine's, is left as it is")

    def test_the_cores_watch_and_sync_may_be_overridden_and_their_intervals_written(self):
        """C-37 C: the sync's interval (sync.interval) and the watch's change their lines; the supervisor's may not;
        apply writes the intervals update.py and the sync count three rounds by."""
        self.all_as_asked()
        before = self.get()
        s = self.settings(core={"watch": {"when": {"every": "30m", "offset": "machine", "after": "13m"}},
                                "supervisor": {"when": {"every": "10m"}}})
        s["sync"] = {"interval": "1h"}
        code, text = self.apply_with(s, write=False)
        self.assertIn("不動 core.schedules.supervisor：不知道（這一筆不用：這一版還不能改（核心只開放 watch、sync、engine-update））", text)
        rep = self.report(settings=s)
        self.assertEqual(sorted((r["plugin"], r["name"]) for r in rep["core"]), [("core", "sync"), ("core", "watch")],
                         "the core's: shown, never automatic")
        self.apply_with(s)
        changed = [(b, a) for b, a in zip(before, self.get()) if b != a]
        self.assertEqual(sorted(a.split(b"# ")[-1] for b, a in changed), [b"altention:core/sync", b"altention:core/watch"])
        sync = [a for b, a in changed if a.endswith(b"core/sync")][0]
        self.assertEqual(sync.split()[1:5], [b"*", b"*", b"*", b"*"], "once an hour")
        with open(applycore.intervals_path()) as f:
            self.assertEqual(json.load(f), {"engine-update": 900, "sync": 3600, "watch": 1800})
        s = self.settings(core={"engine-update": {"enabled": False}})
        code, text = self.apply_with(s)
        self.assertIn("不能關", text)
        with open(applycore.intervals_path()) as f:
            self.assertEqual(json.load(f)["engine-update"], 900, "its line is left: what runs is what is written")

    def test_the_intervals_are_read_from_the_crontab_lines_themselves(self):
        """#code-review on bcc099a: engine-update set to every hour, then an override that cannot be used (held) while
        the watch changes the same round: intervals.json lost engine-update (worked out from the settings), so the sync
        took update.py for stopped after 45 minutes. Read from the lines, it stays 3600; a time changed by hand is
        what is written too; a line with no even interval keeps the value there."""
        def intervals():
            with open(applycore.intervals_path()) as f:
                return json.load(f)
        self.all_as_asked()
        self.apply_with(self.settings(core={"engine-update": {"when": {"every": "1h", "offset": "machine", "after": "3m"}}}))
        self.assertEqual(intervals()["engine-update"], 3600)
        self.apply_with(self.settings(core={"engine-update": {"when": {"every": "7m"}},
                                            "watch": {"when": {"every": "30m", "offset": "machine", "after": "13m"}}}))
        self.assertEqual(intervals(), {"engine-update": 3600, "sync": 900, "watch": 1800})
        sync = [l for l in self.get() if l.endswith(b"# altention:core/sync")][0]
        hand = b"0 */2 * * * " + b" ".join(sync.split()[5:])  # changed by hand: apply leaves it (no --allow-time)
        self.put([hand if l == sync else l for l in self.get()])
        self.apply_with(self.settings())
        self.assertEqual(intervals()["sync"], 7200, "what runs")
        odd = b"5,6,40 * * * * " + b" ".join(sync.split()[5:])
        self.put([odd if l == hand else l for l in self.get()])
        self.apply_with(self.settings())
        self.assertEqual(intervals()["sync"], 7200, "no even interval: the value there is kept")

    def test_nothing_to_change_still_writes_the_intervals(self):
        """#code-review and #code-review-2 on 24d43da: apply returned at 「沒有要改的」 before noting them, so an
        intervals.json gone or stale (a time changed by hand, with nothing else to write) was never put right."""
        def intervals():
            with open(applycore.intervals_path()) as f:
                return json.load(f)
        self.all_as_asked()
        os.remove(applycore.intervals_path())
        code, text = self.apply_with(self.settings())
        self.assertIn("沒有要改的", text)
        self.assertEqual(intervals(), {"engine-update": 900, "sync": 900, "watch": 900}, "written back")
        sync = [l for l in self.get() if l.endswith(b"# altention:core/sync")][0]
        self.put([b"0 */2 * * * " + b" ".join(sync.split()[5:]) if l == sync else l for l in self.get()])
        code, text = self.apply_with(self.settings())
        self.assertIn("沒改（時間不同，要改請加 --allow-time core/sync）", text)
        self.assertIn("沒有要改的", text)
        self.assertEqual(intervals()["sync"], 7200, "the hand-changed line, alone in the run")
        other = b'*/5 * * * * /usr/bin/python3 "/srv/other/engine/altention/cron_sync.py"  # altention:core/sync'
        self.put(self.get() + [other])
        self.apply_with(self.settings())
        self.assertEqual(intervals()["sync"], 7200, "another engine's line with the marker says nothing of this one's")
        code, text = self.apply_with(self.settings(), write=False)
        os.remove(applycore.intervals_path())
        self.apply_with(self.settings(), write=False)
        self.assertFalse(os.path.exists(applycore.intervals_path()), "a dry run writes nothing")

    def test_list_says_where_a_time_comes_from(self):
        """#code-review-2 on bcc099a: sync.interval in effect was said to come from plugins.<名>.schedules."""
        from altention import plugin
        core = plugin.load(os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                                        "altention", "core.plugin.json"), core=True)
        for settings, said in (({"sync": {"interval": "1h"}}, "每 1h（各機器錯開）（實例設定 sync.interval）"),
                               ({"core": {"schedules": {"sync": {"when": {"every": "2h"}}}}}, "每 2h（實例設定 core.schedules）"),
                               ({}, "每 15m（各機器錯開）（plugin.json）")):
            it = next(i for i in plugin.effective_schedules("core", core, settings)[0] if i["name"] == "sync")
            self.assertEqual(plugins.schedule_words(dict(it, core=True)), said, settings)
        self.assertEqual(plugins.schedule_words({"when": {"every": "1h"}, "source": "instance"}), "每 1h（實例設定 plugins.<名>.schedules）")

    def test_cron_interval(self):
        for fields, secs in (("*/10 * * * *", 600), ("3,18,33,48 * * * *", 900), ("33 * * * *", 3600), ("7 */3 * * *", 10800),
                             ("7 2-23/6 * * *", 21600), ("0 3 * * *", 86400), ("8,38 * * * *", 1800), ("0 3 * * 1", None),
                             ("1,2,30 * * * *", None), ("*/7 * * * *", None), ("@reboot", None), ("61 * * * *", None)):
            self.assertEqual(applycore.cron_interval(fields.split()), secs, fields)


class OtherEngine(Rig):
    """C-42 (#code-review-2 on e4ce65f, manager 2026-10-17): this engine's line of an item gone, another engine's line
    with the same marker there: apply adds this engine's line and leaves the other as it is, unless that line's
    folder is one this engine moved from (old_folder_in: a link to this engine, another checkout of it (C-14), or a
    folder gone that --moved-from names): then, as before, it is rewritten in place."""
    BASE = Overrides.BASE
    all_as_asked, settings = Report.all_as_asked, Overrides.settings
    line = Overrides.line

    def apply_with(self, settings, moved_from=()):
        out = io.StringIO()
        engine = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        cfg = plugins.config
        with mock.patch.object(cfg, "instance_dir", return_value=os.path.join(self.tmp, "instance")), \
                mock.patch.object(cfg, "settings", return_value=settings), \
                mock.patch.object(cfg, "setting", return_value=None), \
                mock.patch.object(cfg, "inventory", return_value={"machines": {"host-a": {"roles": ["chatlog", "progress", "usage", "planner"]}, "host-b": {}}}), \
                mock.patch.object(cfg, "this_machine", return_value="host-a"), \
                mock.patch.object(cfg, "bot_machine", return_value=("host-a", {}, False)), \
                mock.patch.object(plugins.plugin, "ENGINE", engine):
            code = plugins.apply(True, (), out, moved_from=list(moved_from))
        return code, out.getvalue()

    def other_instead(self, folder):
        """The crontab as apply made it, this engine's remind line swapped for the same line of `folder`."""
        self.all_as_asked()
        mine = self.line("planner/remind")
        other = mine.replace(self.engine.encode("utf-8"), folder.encode("utf-8"))
        self.assertNotEqual(other, mine)
        self.put([other if l == mine else l for l in self.get()])
        return mine, other

    def test_another_engines_line_is_left_and_this_engines_added(self):
        for folder in ("/srv/other/engine", os.path.join(self.tmp, "not an engine")):  # gone, or there but not an engine
            os.makedirs(os.path.join(self.tmp, "not an engine"), exist_ok=True)
            mine, other = self.other_instead(folder)
            before = self.get()
            code, text = self.apply_with(self.settings())
            self.assertIn("加上：planner/remind", text, folder)
            after = self.get()
            self.assertEqual(after[:len(before)], before, "every line there stays, the other engine's included")
            self.assertEqual(after[len(before):], [mine], "this engine's line added at the end")
            code, text = self.apply_with(self.settings())
            self.assertEqual(self.get(), after, "and the next run changes nothing")
            os.remove(self.file)

    def test_a_folder_this_engine_moved_from_is_rewritten_as_before(self):
        checkout = os.path.join(self.tmp, "another checkout")
        os.makedirs(os.path.join(checkout, "altention"))
        with open(os.path.join(checkout, "altention", "config.py"), "w") as f:
            f.write("")
        for folder, moved in ((checkout, ()), ("/srv/old/engine", ("/srv/old/engine",))):  # C-14; named gone (C-17)
            mine, other = self.other_instead(folder)
            before = self.get()
            code, text = self.apply_with(self.settings(), moved)
            self.assertEqual(self.get(), [mine if l == other else l for l in before], folder)
            os.remove(self.file)


class EngineUnderHome(Rig):
    """#code-review on cba4b4b: this engine's own line written through ${HOME} was not seen as this engine's, and
    apply added a second line for the item (it ran twice). Each way of naming home leaves one line."""

    def setUp(self):
        super().setUp()
        src = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        self.engine = os.path.join(os.path.expanduser("~"), "c42-engine")  # under the throwaway home
        for rel in ["altention/core.plugin.json"] + [os.path.join(d, "plugin.json") for d in os.listdir(src)
                                                     if os.path.isfile(os.path.join(src, d, "plugin.json"))]:
            os.makedirs(os.path.dirname(os.path.join(self.engine, rel)), exist_ok=True)
            shutil.copy(os.path.join(src, rel), os.path.join(self.engine, rel))
        self.addCleanup(shutil.rmtree, self.engine, True)

    def apply(self):
        out = io.StringIO()
        cfg = plugins.config
        with mock.patch.object(cfg, "instance_dir", return_value=os.path.join(self.tmp, "instance")), \
                mock.patch.object(cfg, "settings", return_value=Overrides.BASE), \
                mock.patch.object(cfg, "setting", return_value=None), \
                mock.patch.object(cfg, "inventory", return_value={"machines": {"host-a": {"roles": ["chatlog", "progress", "usage", "planner"]}, "host-b": {}}}), \
                mock.patch.object(cfg, "this_machine", return_value="host-a"), \
                mock.patch.object(cfg, "bot_machine", return_value=("host-a", {}, False)), \
                mock.patch.object(plugins.plugin, "ENGINE", self.engine):
            os.makedirs(os.path.join(self.tmp, "instance"), exist_ok=True)
            code = plugins.apply(True, (), out)
        return code, out.getvalue()

    def test_each_way_of_naming_home_leaves_one_line(self):
        rest = os.path.relpath(self.engine, os.path.expanduser("~"))
        for form in ("${HOME}/" + rest, '"$HOME/%s' % rest, "~/" + rest, "$HOME/" + rest):
            self.put([b"SHELL=/bin/bash"])
            self.apply()
            mine = [l for l in self.get() if l.endswith(b"# altention:planner/remind")]
            self.assertEqual(len(mine), 1)
            text = mine[0].decode("utf-8")
            quoted = form.startswith('"')
            path = '"%s/planner/remind.py"' % self.engine
            self.assertIn(path, text)
            changed = text.replace(path, form + '/planner/remind.py' + ('"' if quoted else ""))
            changed = changed.replace(">>", "> ")  # its output not as asked either: not "same", so apply looks for it by name
            self.put([l if l != mine[0] else changed.encode("utf-8") for l in self.get()])
            code, text = self.apply()
            got = [l for l in self.get() if l.endswith(b"# altention:planner/remind")]
            self.assertEqual(len(got), 1, (form, text))
            self.assertEqual(got[0], mine[0], (form, "rewritten in place as asked"))
            os.remove(self.file)

