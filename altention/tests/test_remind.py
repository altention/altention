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

    def test_a_machine_installed_before_gets_them_on_the_next_round(self):
        os.makedirs(os.path.dirname(self.default), mode=0o755)
        with open(self.default, "w") as f:
            json.dump({"items": []}, f)
        with mock.patch.object(remind.subprocess, "run", side_effect=AssertionError("nothing due")):
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
        with mock.patch.object(remind.subprocess, "run", side_effect=fake_run), mock.patch("sys.stderr", err):
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
        self.assertEqual(plugin.schedule_files(self.d, PLANNER, set_by_hand), [os.path.expanduser("~/mine.json")])
        self.assertIsNone(plugin.default_schedule_file(self.d, PLANNER, set_by_hand), "a setting wins")

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
        with mock.patch.object(remind.subprocess, "run", side_effect=fake_run):
            self.assertEqual(remind.main([]), 0)
            self.assertEqual(posted, [["dc", "post", "chat", "看牙醫"]])
            self.assertEqual(remind.main([]), 0)
        self.assertEqual(len(posted), 1, "a once item is sent once")

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
        for word in ("⏰", "🌙", "📅", "plan.md", "reminders.json", "沒另外設定", "remind.py --check", "--where", "#secretary", "700"):
            self.assertIn(word, text)

    def test_the_messages_and_the_pitch(self):
        with open(os.path.join(PLANNER, "plugin.json"), encoding="utf-8") as f:
            d = json.load(f)
        sent = [s["do"]["message"] for s in d["schedules"] if "message" in s["do"]]
        self.assertEqual(len(sent), 3)
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
