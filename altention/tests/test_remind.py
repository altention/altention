"""planner's reminders on a new install (owner's install 2026-10-15): planner.schedule_files was never set, so
apply could not write the remind line and no reminder was ever sent. With nothing set, the default file
{data}/planner/reminders.json is used, made 700/600 on first use, and a reminder in it is sent."""
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
from contextlib import redirect_stdout
from unittest import mock

from altention import plugin

ENGINE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PLANNER = os.path.join(ENGINE, "planner")
spec = importlib.util.spec_from_file_location("remind", os.path.join(PLANNER, "remind.py"))
remind = importlib.util.module_from_spec(spec)
spec.loader.exec_module(remind)


REAL_RUN = subprocess.run


def git_passes(fake):
    """A stand-in for subprocess.run that lets git through (the secretary's folder is a git repo, C-26) and hands
    every other command to `fake` (an exception: raised)."""
    def run(cmd, *a, **k):
        if list(cmd)[:1] == ["git"]:
            return REAL_RUN(cmd, *a, **k)
        if isinstance(fake, BaseException):
            raise fake
        return fake(cmd, *a, **k)
    return run


def mode(path):
    return stat.S_IMODE(os.stat(path).st_mode)


class NothingSet(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.inst = os.path.join(self.tmp, "instance")
        os.makedirs(self.inst)
        self.settings = {"format": 1, "core": {"timezone": "UTC"}}  # a new instance: no planner key at all
        with open(os.path.join(self.inst, "plugins.json"), "w") as f:
            json.dump(self.settings, f)
        self.data = os.path.join(self.tmp, "data")
        self.default = os.path.join(self.data, "planner", "reminders.json")
        self.patches = [mock.patch.dict(os.environ, {"ALT_INSTANCE": self.inst, "ALT_DATA": self.data}),
                        mock.patch.object(remind, "STATE", os.path.join(self.tmp, "remind-state.json"))]
        for p in self.patches:
            p.start()
        with open(os.path.join(PLANNER, "plugin.json"), encoding="utf-8") as f:
            self.d = json.load(f)
        self.umask = os.umask(0o022)

    def tearDown(self):
        os.umask(self.umask)
        for p in self.patches:
            p.stop()
        shutil.rmtree(self.tmp)

    def test_the_secretarys_folder_is_made_private_with_its_claude_md_and_plan(self):
        """#secretary works in {data}/planner (owner 2026-10-15): its CLAUDE.md (planner's template) and plan.md are put
        there on first use and never written over (the owner may change them)."""
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(remind.main(["--secretary"]), 0)
        folder = out.getvalue().strip()
        self.assertEqual(folder, os.path.join(self.data, "planner"))
        self.assertEqual(mode(folder), 0o700)
        with open(os.path.join(PLANNER, "CLAUDE.template.md"), encoding="utf-8") as f:
            template = f.read()
        with open(os.path.join(folder, "CLAUDE.md"), encoding="utf-8") as f:
            self.assertEqual(f.read(), template)
        for n in ("CLAUDE.md", "plan.md"):
            self.assertEqual(mode(os.path.join(folder, n)), 0o600, n)
        with open(os.path.join(folder, "CLAUDE.md"), "a", encoding="utf-8") as f:
            f.write("- 早報用英文\n")
        with redirect_stdout(io.StringIO()):
            self.assertEqual(remind.main(["--secretary"]), 0)
        with open(os.path.join(folder, "CLAUDE.md"), encoding="utf-8") as f:
            self.assertTrue(f.read().endswith("- 早報用英文\n"), "kept")

    def write_settings(self, **extra):
        with open(os.path.join(self.inst, "plugins.json"), "w") as f:
            json.dump(dict(self.settings, **extra), f)

    def items(self, path=None):
        with open(path or self.default, encoding="utf-8") as f:
            return json.load(f)["items"]

    def test_the_secretary_gets_the_briefings_at_the_times_chosen(self):
        """Owner 2026-10-16: the times were fixed in plugin.json. Opening the secretary puts the three into its schedule
        file at the times the install asked (planner.briefings), each one not set at its default."""
        self.write_settings(planner={"briefings": {"morning": {"at": "07:30", "days": "daily"},
                                                   "weekly": {"at": "10:00", "days": ["sat"]}}})
        code, folder, err = self.secretary()
        self.assertEqual((code, folder, err), (0, os.path.dirname(self.default), ""))
        self.assertEqual(mode(self.default), 0o600)
        self.assertEqual(self.items(), [
            {"name": "morning", "when": {"at": "07:30", "days": "daily"}, "do": {"message": remind.BRIEFINGS[0][2]}},
            {"name": "evening", "when": {"at": "22:00", "days": "daily"}, "do": {"message": remind.BRIEFINGS[1][2]}},
            {"name": "weekly", "when": {"at": "10:00", "days": ["sat"]}, "do": {"message": remind.BRIEFINGS[2][2]}}])
        out = io.StringIO()
        with redirect_stdout(out):
            remind.main(["--check"])
        self.assertEqual([l.split(" · ", 1)[1] for l in out.getvalue().splitlines()],
                         ["morning：開 每天 07:30", "evening：開 每天 22:00", "weekly：開 每週六 10:00"])

    def test_the_briefings_go_by_the_instances_zone_not_the_machines(self):
        """C-37 point 4 (manager 2026-10-17): the old plugin.json lines said "timezone": "machine"; the briefings in the
        schedule file say no zone, so remind goes by core.timezone whatever the machine's clock is set to."""
        from altention import scheduler
        self.write_settings(core={"timezone": "Asia/Taipei"})
        self.assertEqual(self.secretary()[0], 0)
        self.assertFalse([it for it in self.items() if "timezone" in it])
        with mock.patch.dict(os.environ, {"TZ": "UTC"}):
            zone, _now = remind.zone_now()
            for it in scheduler.load(self.default)["items"]:
                self.assertTrue(scheduler.needs_zone(it), it["name"])
                self.assertIs(scheduler.zone_for(it, zone), zone, it["name"])
        self.assertEqual(zone.offset_at(0), 8 * 3600, "Asia/Taipei, not the machine's UTC")

    def test_the_secretary_again_adds_nothing_and_keeps_what_was_changed(self):
        self.assertEqual(self.secretary()[0], 0)
        items = self.items()
        items[0]["when"] = {"at": "06:00", "days": "daily"}  # the secretary changed one, turned one off
        items[1]["enabled"] = False
        items.append({"name": "dentist", "when": {"once": "2099-01-01 09:00"}, "do": {"message": "看牙醫", "channel": "chat"}})
        with open(self.default, "w", encoding="utf-8") as f:
            json.dump({"items": items}, f)
        self.write_settings(planner={"briefings": {"morning": {"at": "09:00", "days": "daily"}}})  # asked again later
        self.assertEqual(self.secretary()[0], 0)
        self.assertEqual(self.items(), items, "the same folder again: nothing added twice, nothing written over")

    def test_only_the_missing_ones_are_added_to_a_file_there_before(self):
        os.makedirs(os.path.dirname(self.default))
        with open(self.default, "w") as f:
            json.dump({"items": [{"name": "weekly", "when": {"at": "19:00", "days": ["fri"]}, "do": {"message": "週回顧"}}]}, f)
        self.assertEqual(self.secretary()[0], 0)
        self.assertEqual([(i["name"], i["when"]["at"]) for i in self.items()], [("weekly", "19:00"), ("morning", "08:00"),
                                                                                 ("evening", "22:00")])

    def test_added_to_a_repo_there_before_they_are_committed(self):
        code, folder, _ = self.secretary()
        with open(self.default, "w") as f:
            json.dump({"items": []}, f)  # an install from before: an empty schedule file, committed
        self.git(folder, "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-qam", "empty")
        self.assertEqual(self.secretary()[0], 0)
        self.assertEqual([i["name"] for i in self.items()], ["morning", "evening", "weekly"])
        self.assertEqual(self.git(folder, "status", "--porcelain").stdout, "")
        self.assertEqual(self.git(folder, "rev-list", "--count", "HEAD").stdout.strip(), "3", "made, emptied, added")

    def at(self, hour, minute, run):
        """`run` with the clock at that time (UTC, the instance's zone here) on Monday 2026-01-05."""
        import calendar
        with mock.patch.object(remind.time, "time", return_value=calendar.timegm((2026, 1, 5, hour, minute, 0))):
            return run()

    def posted_at(self, hour, minute):
        posted = []

        def fake_run(argv, *a, **kw):
            posted.append(argv[-1])
            return subprocess.CompletedProcess(argv, 0)
        with mock.patch.object(remind.subprocess, "run", side_effect=git_passes(fake_run)), \
                mock.patch("sys.stderr", io.StringIO()):
            self.at(hour, minute, lambda: remind.main([]))
        return posted

    def test_moved_after_the_time_it_is_not_sent_again_and_before_it_is_sent(self):
        """#code-review of 5dcd0a1: the old cron line had sent the morning brief at 08:00; moved at 08:30, the round at
        08:35 sent it again (an item with no state is made up for within LATE hours). Marked sent when added late."""
        self.write_settings(planner={"channel": "100000000000000001"})
        self.assertEqual(self.at(8, 30, self.secretary)[0], 0)
        self.assertEqual(self.posted_at(8, 35), [])
        self.assertEqual(self.posted_at(22, 1), [remind.BRIEFINGS[1][2]], "the evening one later that day, as usual")

    def test_moved_before_the_time_it_is_sent_at_it(self):
        self.write_settings(planner={"channel": "100000000000000001"})
        self.assertEqual(self.at(7, 50, self.secretary)[0], 0)
        self.assertEqual(self.posted_at(7, 55), [])
        self.assertEqual(self.posted_at(8, 0), [remind.BRIEFINGS[0][2]])
        self.assertEqual(self.posted_at(8, 5), [], "once")

    def test_moved_long_after_the_time_nothing_is_marked(self):
        self.write_settings(planner={"channel": "100000000000000001"})
        self.assertEqual(self.at(11, 5, self.secretary)[0], 0)
        self.assertFalse(os.path.exists(remind.STATE), "nothing due: the state is left as it was")
        self.assertEqual(self.posted_at(11, 10), [])

    def test_a_time_that_cannot_be_read_adds_nothing_and_says_which(self):
        self.write_settings(planner={"briefings": {"evening": {"at": "25:00", "days": "daily"}}})
        code, folder, err = self.secretary()
        self.assertEqual((code, folder), (1, os.path.dirname(self.default)), "the folder still printed for the channel")
        self.assertIn("planner.briefings.evening", err)
        self.assertFalse(os.path.exists(self.default))
        self.write_settings(planner={"briefings": {"weekly": {"every": "10m"}}})
        code, _, err = self.secretary()
        self.assertEqual(code, 1)
        self.assertIn("planner.briefings.weekly", err)

    def test_the_briefings_go_into_the_secretarys_file_never_another_channels(self):
        """Manager 2026-10-16: a machine's planner.schedule_files listed ~/english/schedule.json first (another channel's
        file); --secretary wrote into the first one. Now always into {data}/planner/reminders.json."""
        mine = os.path.join(self.tmp, "english.json")
        self.write_settings(planner={"schedule_files": [mine]})
        code, _, err = self.secretary()
        self.assertEqual((code, err), (0, ""))
        self.assertFalse(os.path.exists(mine), "another channel's file: not made")
        self.assertEqual([i["name"] for i in self.items()], ["morning", "evening", "weekly"])
        with open(mine, "w") as f:
            json.dump({"items": [{"name": "lesson", "when": {"at": "00:30", "days": "daily"},
                                  "do": {"message": "上課", "channel": "english"}}]}, f)
        before = open(mine).read()
        self.assertEqual(self.secretary()[0], 0)
        self.assertEqual(open(mine).read(), before, "left exactly as it was")
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(remind.main(["--where"]), 0)
        self.assertEqual(out.getvalue().split(), [self.default, mine], "both read, the secretary's first")
        out = io.StringIO()
        with redirect_stdout(out):
            remind.main(["--check"])
        self.assertEqual(sorted(l.split(" · ")[1].split("：")[0] for l in out.getvalue().splitlines()),
                         ["evening", "lesson", "morning", "weekly"])

    def secretary(self):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), mock.patch("sys.stderr", err):
            code = remind.main(["--secretary"])
        return code, out.getvalue().strip(), err.getvalue()

    def git(self, folder, *args):
        return REAL_RUN(["git", "-C", folder] + list(args), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                        universal_newlines=True)

    def bare(self, name, files=None):
        bare = os.path.join(self.tmp, name + ".git")
        REAL_RUN(["git", "init", "-q", "--bare", bare])
        REAL_RUN(["git", "-C", bare, "symbolic-ref", "HEAD", "refs/heads/main"])
        if files:
            work = os.path.join(self.tmp, name + ".work")
            REAL_RUN(["git", "clone", "-q", bare, work], stderr=subprocess.DEVNULL)
            for n, text in files.items():
                with open(os.path.join(work, n), "w") as f:
                    f.write(text)
            for args in (["checkout", "-q", "-b", "main"], ["add", "-A"],
                         ["-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-qm", "x"], ["push", "-q", "origin", "main"]):
                self.git(work, *args)
        return bare

    def set_remote(self, url):
        with open(os.path.join(self.inst, "plugins.json"), "w") as f:
            json.dump(dict(self.settings, sync={"dirs": {"planner": {"remote": url}}}), f)

    def test_the_secretarys_folder_is_a_git_repo_with_a_first_commit(self):
        """C-26: the secretary commits what it changes (and the sync after it): its folder is a repo from the start."""
        code, folder, err = self.secretary()
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(self.git(folder, "rev-list", "--count", "HEAD").stdout.strip(), "1")
        self.assertEqual(sorted(self.git(folder, "ls-files").stdout.split()), [".gitignore", "CLAUDE.md", "plan.md", "reminders.json"])
        with open(os.path.join(folder, ".gitignore")) as f:
            self.assertEqual(f.read(), "*.tmp\n")
        self.assertEqual(self.git(folder, "remote").stdout, "", "no remote set: none")
        self.assertEqual(mode(folder), 0o700)
        self.assertEqual(self.secretary()[0], 0)
        self.assertEqual(self.git(folder, "rev-list", "--count", "HEAD").stdout.strip(), "1", "again: no new commit")

    def test_a_folder_there_before_is_made_a_repo_with_nothing_moved(self):
        folder = os.path.join(self.data, "planner")
        os.makedirs(folder)
        with open(os.path.join(folder, "notes.md"), "w") as f:
            f.write("mine\n")
        code, _out, err = self.secretary()
        self.assertEqual((code, err), (0, ""))
        with open(os.path.join(folder, "notes.md")) as f:
            self.assertEqual(f.read(), "mine\n")
        self.assertIn("notes.md", self.git(folder, "ls-files").stdout.split())

    def test_a_new_machine_gets_the_plan_back_from_its_remote(self):
        bare = self.bare("planner-backup", {"plan.md": "# 我的計畫\n", "CLAUDE.md": "mine\n"})
        self.set_remote(bare)
        code, folder, err = self.secretary()
        self.assertEqual(code, 0, err)
        with open(os.path.join(folder, "plan.md"), encoding="utf-8") as f:
            self.assertEqual(f.read(), "# 我的計畫\n", "the backup's, not a new empty plan")
        with open(os.path.join(folder, "CLAUDE.md"), encoding="utf-8") as f:
            self.assertEqual(f.read(), "mine\n")
        self.assertEqual(self.git(folder, "remote", "get-url", "origin").stdout.strip(), bare)
        self.assertTrue(os.path.isfile(os.path.join(folder, ".git", "hooks", "pre-push")))
        self.assertEqual(mode(folder), 0o700)

    def test_a_backup_cloned_back_gets_the_gitignore_and_a_commit_of_what_is_new(self):
        """Review of d1d71c8: cloned, the folder was a repo already and make_repo did nothing."""
        self.set_remote(self.bare("planner-backup", {"plan.md": "# 我的計畫\n"}))
        code, folder, err = self.secretary()
        self.assertEqual(code, 0, err)
        self.assertEqual(sorted(self.git(folder, "ls-files").stdout.split()), [".gitignore", "CLAUDE.md", "plan.md", "reminders.json"])
        self.assertEqual(self.git(folder, "status", "--porcelain").stdout, "", "nothing left untracked")
        self.assertEqual(self.git(folder, "rev-list", "--count", "HEAD").stdout.strip(), "2", "the backup's and one")
        with open(os.path.join(folder, "plan.md"), encoding="utf-8") as f:
            self.assertEqual(f.read(), "# 我的計畫\n")
        self.assertEqual(self.secretary()[0], 0)
        self.assertEqual(self.git(folder, "rev-list", "--count", "HEAD").stdout.strip(), "2", "again: no new commit")

    def test_an_empty_backup_cloned_gets_a_first_commit_on_main(self):
        self.set_remote(self.bare("planner-backup"))
        code, folder, err = self.secretary()
        self.assertEqual(code, 0, err)
        self.assertEqual(self.git(folder, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip(), "main")
        self.assertEqual(self.git(folder, "rev-list", "--count", "HEAD").stdout.strip(), "1")
        self.assertEqual(sorted(self.git(folder, "ls-files").stdout.split()), [".gitignore", "CLAUDE.md", "plan.md", "reminders.json"])
        self.assertTrue(self.git(folder, "remote", "get-url", "origin").stdout.strip().endswith("planner-backup.git"))

    def test_a_remote_holding_other_data_is_not_cloned(self):
        """Review of d1d71c8: a new machine cloned the setting unchecked (one naming another repo made the
        secretary's folder a copy of it)."""
        self.set_remote(self.bare("someone-else", {"README.md": "not a plan\n"}))
        sys.path.insert(0, os.path.join(ENGINE, "ops"))
        import remote as altremote
        with mock.patch.object(altremote, "alert") as told:
            code, folder, err = self.secretary()
        self.assertEqual(code, 0, "the folder still works, made here")
        self.assertIn("沒有從設定的遠端", err)
        self.assertIn("clone-refused", [c.args[0] for c in told.call_args_list])
        self.assertFalse(os.path.exists(os.path.join(folder, "README.md")), "nothing of it here")
        self.assertEqual(self.git(folder, "remote").stdout, "", "nor its origin")
        self.assertEqual(sorted(self.git(folder, "ls-files").stdout.split()), [".gitignore", "CLAUDE.md", "plan.md", "reminders.json"])

    def test_a_repo_without_a_remote_gets_the_one_set_checked_and_a_public_one_is_not_taken(self):
        code, folder, _err = self.secretary()
        self.set_remote(self.bare("planner-backup"))
        code, _out, err = self.secretary()
        self.assertEqual(code, 0)
        self.assertIn("接上", err, "said once, when it is attached")
        self.assertTrue(self.git(folder, "remote", "get-url", "origin").stdout.strip().endswith("planner-backup.git"))
        self.assertEqual(self.secretary()[2], "", "as it should be: nothing said")
        self.git(folder, "remote", "remove", "origin")
        sys.path.insert(0, os.path.join(ENGINE, "ops"))
        import remote as altremote
        with mock.patch.object(altremote, "anonymous", return_value=("public", "anyone")), \
                mock.patch.object(altremote, "alert"):
            code, _out, err = self.secretary()
        self.assertEqual(code, 0, "the folder still works")
        self.assertIn("沒有接上", err)
        self.assertEqual(self.git(folder, "remote").stdout, "")

    def test_a_machine_installed_before_gets_them_on_the_next_round(self):
        os.makedirs(os.path.dirname(self.default), mode=0o755)
        with open(self.default, "w") as f:
            json.dump({"items": []}, f)
        with mock.patch.object(remind.subprocess, "run", side_effect=git_passes(AssertionError("nothing due"))):
            self.assertEqual(remind.main([]), 0)  # the cron line, every 5 minutes
        folder = os.path.dirname(self.default)
        self.assertEqual(mode(folder), 0o700)
        self.assertTrue(os.path.isfile(os.path.join(folder, "CLAUDE.md")))
        self.assertTrue(os.path.isfile(os.path.join(folder, "plan.md")))

    def test_check_before_any_reminder_says_there_is_none_yet(self):
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(remind.main(["--check"]), 0)
        self.assertEqual(out.getvalue().strip(), "%s：還沒有提醒（檔案還不存在）" % self.default)
        self.assertFalse(os.path.exists(self.default), "--check makes nothing")
        bad = os.path.join(self.tmp, "bad.json")
        with open(bad, "w") as f:
            f.write("{ not json")
        out = io.StringIO()
        with redirect_stdout(out):
            remind.main(["--check", bad])
        self.assertIn("讀不懂", out.getvalue(), "a file that is there and broken is still said as such")

    def test_a_folder_of_the_owners_own_is_used_as_it_is(self):
        """planner.folder (C-17: our secretary works in ~/life): that folder is the secretary's; nothing is made,
        changed or written in it (no CLAUDE.md, no plan.md, its mode kept). One that is not there is said."""
        own = os.path.join(self.tmp, "my life")
        os.makedirs(own)
        os.chmod(own, 0o755)
        with open(os.path.join(self.inst, "plugins.json"), "w") as f:
            json.dump(dict(self.settings, planner={"folder": own}), f)
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), mock.patch("sys.stderr", err):
            self.assertEqual(remind.main(["--secretary"]), 0)
        self.assertEqual((out.getvalue().strip(), err.getvalue()), (own, ""))
        self.assertEqual(os.listdir(own), [], "nothing written in it")
        self.assertEqual(mode(own), 0o755, "its mode kept")
        self.assertEqual([i["name"] for i in self.items()], ["morning", "evening", "weekly"],
                         "the schedule file the 5-minute round would make, made the same way (#code-review of 5dcd0a1)")
        with open(os.path.join(self.inst, "plugins.json"), "w") as f:
            json.dump(dict(self.settings, planner={"folder": os.path.join(self.tmp, "not there")}), f)
        err = io.StringIO()
        with redirect_stdout(io.StringIO()), mock.patch("sys.stderr", err):
            self.assertEqual(remind.main(["--secretary"]), 1)
        self.assertIn("planner.folder", err.getvalue())

    def test_an_item_with_no_channel_goes_to_the_secretary(self):
        with redirect_stdout(io.StringIO()):
            remind.main(["--where"])
        now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
        at = (now - datetime.timedelta(minutes=1)).strftime("%Y-%m-%d %H:%M")
        with open(self.default, "w") as f:
            json.dump({"items": [{"name": "a", "when": {"once": at}, "do": {"message": "倒垃圾"}}]}, f)
        posted = []
        err = io.StringIO()

        def fake_run(argv, *a, **kw):
            posted.append(argv[1:])
            return subprocess.CompletedProcess(argv, 0)
        with mock.patch.object(remind.subprocess, "run", side_effect=git_passes(fake_run)), mock.patch("sys.stderr", err):
            remind.main([])  # planner.channel not set: said, not sent
            self.assertEqual(posted, [])
            self.assertIn("planner.channel 也沒設", err.getvalue())
            with open(os.path.join(self.inst, "plugins.json"), "w") as f:
                json.dump(dict(self.settings, planner={"channel": "42"}), f)
            remind.main([])
        self.assertEqual(posted, [["dc", "post", "42", "倒垃圾"]])

    def test_the_remind_line_can_be_written_and_reads_the_default(self):
        remind_item = [s for s in self.d["schedules"] if s["name"] == "remind"][0]
        line = plugin.expand(remind_item["do"]["run"], PLANNER, self.settings)  # PluginError before: setting not set
        self.assertIn("remind.py", line)
        self.assertEqual(plugin.schedule_files(self.d, PLANNER, self.settings), [self.default])
        set_by_hand = dict(self.settings, planner={"schedule_files": "~/mine.json"})
        self.assertEqual(plugin.schedule_files(self.d, PLANNER, set_by_hand), [self.default, os.path.expanduser("~/mine.json")],
                         "the secretary's own file is read whatever the setting lists, first (manager 2026-10-17)")
        self.assertEqual(plugin.default_schedule_file(self.d, PLANNER, set_by_hand), self.default)
        named = self.default.replace(os.path.expanduser("~"), "~", 1) if self.default.startswith(os.path.expanduser("~")) \
            else self.default
        listed = dict(self.settings, planner={"schedule_files": ["~/a.json", named, self.default + "/../reminders.json"]})
        self.assertEqual(plugin.schedule_files(self.d, PLANNER, listed), [self.default, os.path.expanduser("~/a.json")],
                         "listed too (as a machine did by hand, ~ or ..): once")

    def test_where_makes_the_default_private(self):
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(remind.main(["--where"]), 0)
        self.assertEqual(out.getvalue().strip(), self.default)
        self.assertEqual(mode(os.path.dirname(self.default)), 0o700)
        self.assertEqual(mode(self.default), 0o600)
        with open(self.default) as f:
            self.assertEqual(json.load(f), {"items": []})

    def test_a_folder_already_there_and_open_is_made_private(self):
        os.makedirs(os.path.dirname(self.default), mode=0o755)
        os.chmod(os.path.dirname(self.default), 0o755)
        with redirect_stdout(io.StringIO()):
            remind.main(["--where"])
        self.assertEqual(mode(os.path.dirname(self.default)), 0o700)

    def test_check_says_times_as_a_person_reads_them(self):
        with redirect_stdout(io.StringIO()):
            remind.main(["--where"])
        with open(self.default, "w") as f:
            json.dump({"items": [{"name": "a", "when": {"at": "21:00", "days": "daily"}, "do": {"message": "x", "channel": "c"}},
                                 {"name": "b", "when": {"at": "20:30", "days": ["sun"]}, "do": {"message": "x", "channel": "c"}},
                                 {"name": "c", "when": {"once": "2026-10-20 09:05"}, "do": {"message": "x", "channel": "c"}},
                                 {"name": "d", "enabled": False, "at": "07:00", "days": ["mon", "thu"], "text": "x"}],
                       "channel": "c"}, f)
        out = io.StringIO()
        with redirect_stdout(out):
            remind.main(["--check"])
        lines = [l.split(" · ", 1)[1] for l in out.getvalue().splitlines()]
        self.assertEqual(lines, ["a：開 每天 21:00", "b：開 每週日 20:30", "c：開 2026-10-20 09:05（一次）", "d：關 每週一、四 07:00"])

    def test_a_reminder_in_the_default_file_is_sent_once(self):
        with redirect_stdout(io.StringIO()):
            remind.main(["--where"])
        now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
        at = (now - datetime.timedelta(minutes=1)).strftime("%Y-%m-%d %H:%M")
        with open(self.default, "w") as f:
            json.dump({"items": [{"name": "dentist", "when": {"once": at}, "do": {"message": "看牙醫", "channel": "chat"}},
                                 {"name": "later", "when": {"once": "2099-01-01 09:00"}, "do": {"message": "x", "channel": "chat"}}]}, f)
        posted = []

        def fake_run(argv, *a, **kw):  # Discord is a stand-in: bin/altention dc post <channel> <text>
            posted.append(argv[1:])
            return subprocess.CompletedProcess(argv, 0)
        with mock.patch.object(remind.subprocess, "run", side_effect=git_passes(fake_run)):
            self.assertEqual(remind.main([]), 0)
            self.assertEqual(posted, [["dc", "post", "chat", "看牙醫"]])
            self.assertEqual(remind.main([]), 0)
        self.assertEqual(len(posted), 1, "a once item is sent once")

    def test_with_a_folder_of_the_owners_own_the_default_folder_holds_only_the_schedule_file(self):
        """#code-review of 044c5e8: planner.folder set (the owner's own repo) and schedule_files listing the default
        and another file, as one of our machines has it: --where, a 5-minute round, apply made a second secretary's
        folder ({data}/planner with CLAUDE.md, plan.md, a repo and a commit). Now only the folder and the file."""
        own = os.path.join(self.tmp, "life")
        os.makedirs(own)
        subprocess.run(["git", "init", "-q", own], check=True)
        with open(os.path.join(own, "plan.md"), "w") as f:
            f.write("mine\n")
        subprocess.run(["git", "-C", own, "-c", "user.name=t", "-c", "user.email=t@example.com", "add", "-A"], check=True)
        subprocess.run(["git", "-C", own, "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-qm", "mine"],
                       check=True)
        other = os.path.join(self.tmp, "english.json")
        with open(other, "w") as f:
            json.dump({"items": []}, f)
        with open(os.path.join(self.inst, "plugins.json"), "w") as f:
            json.dump(dict(self.settings, planner={"folder": own, "schedule_files": [self.default, other]}), f)
        folder = os.path.dirname(self.default)

        def only_the_file(when):
            self.assertEqual(sorted(os.listdir(folder)), ["reminders.json"], when)
            self.assertEqual(mode(folder), 0o700, when)
        with redirect_stdout(io.StringIO()):
            self.assertEqual(remind.main(["--where"]), 0)
        only_the_file("--where")
        os.remove(self.default)
        os.rmdir(folder)
        with redirect_stdout(io.StringIO()), mock.patch.object(remind.subprocess, "run", side_effect=git_passes(lambda *a, **k: None)):
            remind.main([])
        only_the_file("a 5-minute round")
        os.remove(self.default)
        os.rmdir(folder)
        import sys
        sys.path.insert(0, os.path.join(ENGINE, "ops"))
        import applycore
        rows = [("planner", PLANNER, self.d, "on", "")]
        files = applycore.schedule_files(rows, dict(self.settings, planner={"folder": own, "schedule_files": [self.default, other]}))
        applycore.create_missing(files, "host-a", defaults=applycore.default_schedule_files(rows, self.settings))
        only_the_file("apply")
        with redirect_stdout(io.StringIO()), mock.patch("sys.stderr", io.StringIO()):
            self.assertEqual(remind.main(["--secretary"]), 0)
        only_the_file("--secretary")
        with open(self.default, encoding="utf-8") as f:
            got = json.load(f)  # apply makes it a bare list ("[]"), a schedule all the same
        self.assertEqual([i["name"] for i in got], ["morning", "evening", "weekly"])
        log = subprocess.run(["git", "-C", own, "log", "--oneline"], stdout=subprocess.PIPE, universal_newlines=True).stdout
        self.assertEqual((len(log.splitlines()), sorted(os.listdir(own))), (1, [".git", "plan.md"]), "the owner's repo as it was")

    def test_apply_makes_the_default_folder_and_file(self):
        import sys
        sys.path.insert(0, os.path.join(ENGINE, "ops"))
        import applycore
        rows = [("planner", PLANNER, self.d, "on", "")]
        files = applycore.schedule_files(rows, self.settings)
        defaults = applycore.default_schedule_files(rows, self.settings)
        self.assertEqual((files, defaults), ([("planner", self.default)], {self.default}))
        said = applycore.create_missing(files, "host-a", defaults=defaults)
        self.assertEqual(said, ["建空的排程檔（planner）：%s" % self.default])
        self.assertEqual((mode(os.path.dirname(self.default)), mode(self.default)), (0o700, 0o600))
        os.remove(self.default)
        os.chmod(os.path.dirname(self.default), 0o755)  # there already, and open
        applycore.create_missing(files, "host-a", defaults=defaults)
        self.assertEqual(mode(os.path.dirname(self.default)), 0o700)
        other = os.path.join(self.tmp, "nofolder", "x.json")
        self.assertIn("所在的資料夾不存在", applycore.create_missing([("p", other)], "host-a")[0], "a typed path: no folder made")


class TheSecretarysGuide(unittest.TestCase):
    """Owner's 22:00 review 2026-10-15: the messages said 「照 CLAUDE.md」 and #chat (the instance) has none. The
    secretary gets its own channel working in {data}/planner, where planner puts its CLAUDE.md; a machine installed
    before (secretary still in #chat) is pointed at the template by the global notes and told by doctor how to move."""

    def test_the_template_is_there_and_names_what_exists(self):
        with open(os.path.join(PLANNER, "CLAUDE.template.md"), encoding="utf-8") as f:
            text = f.read()
        for word in ("⏰", "🌙", "📅", "plan.md", "reminders.json", "印出的第一個檔", "remind.py --check", "--where", "#secretary", "700",
                     "morning", "evening", "weekly", '"enabled": false', "改時間"):
            self.assertIn(word, text)

    def test_the_messages_and_the_pitch(self):
        with open(os.path.join(PLANNER, "plugin.json"), encoding="utf-8") as f:
            d = json.load(f)
        self.assertEqual([s["do"] for s in d["schedules"] if "message" in s["do"]], [],
                         "the three wake-ups are in the secretary's schedule file now (owner 2026-10-16)")
        sent = [m for _, _, m in remind.BRIEFINGS]
        self.assertEqual([m[0] for m in sent], ["⏰", "🌙", "📅"])
        self.assertTrue(all("照 CLAUDE.md" in m for m in sent), "true now: the secretary's folder has one")
        self.assertIn("#secretary", d["pitch"])
        self.assertNotIn("#chat", json.dumps(d, ensure_ascii=False))

    def test_the_global_notes_point_older_installs_at_the_template(self):
        with open(os.path.join(ENGINE, "CLAUDE.global.md"), encoding="utf-8") as f:
            lines = [l for l in f if "CLAUDE.template.md" in l]
        self.assertEqual(len(lines), 1)
        rel = lines[0].split("`{engine}/", 1)[1].split("`", 1)[0]
        self.assertTrue(os.path.isfile(os.path.join(ENGINE, rel)), rel)
        for mark in ("⏰", "🌙", "📅", "照 CLAUDE.md", "remind.py --secretary", "#secretary"):
            self.assertIn(mark, lines[0])


if __name__ == "__main__":
    unittest.main()
