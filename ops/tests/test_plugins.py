"""ops/plugins.py show: where each plugin stands on a machine, checked against the machine list's roles."""
import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))))
from altention import testing as _testing  # noqa: E402
_testing.guard_home()  # tests never touch the real home (altention/testing.py)
import io
import json
import re
import os
import shutil
import tempfile
import unittest
from unittest import mock

from ops import plugins


def write(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(data if isinstance(data, str) else json.dumps(data))


class Show(unittest.TestCase):
    def setUp(self):
        self.inst = tempfile.mkdtemp(prefix="實例 ")
        self.env = mock.patch.dict(os.environ, {"ALT_INSTANCE": self.inst, "ALT_ROOT": os.path.join(self.inst, "root")})
        self.env.start()
        write(os.path.join(self.inst, "plugins.json"), {"format": 1})

    def tearDown(self):
        self.env.stop()
        shutil.rmtree(self.inst, ignore_errors=True)

    def run_show(self, roles_b, json_out=False):
        write(os.path.join(self.inst, "machines.json"), {"format": 1, "machines": {
            "host-a": {"ssh": ["local"], "roles": ["chatlog", "dcbot", "progress", "planner", "usage"]},
            "host-b": {"ssh": ["host-b"], "roles": roles_b}}})
        out = io.StringIO()
        code = plugins.show("host-b", json_out, out)
        return code, out.getvalue()

    def test_placements_from_plugin_json_alone(self):
        code, text = self.run_show(["chatlog", "dcattach", "usage"])
        self.assertEqual(code, 0, text)
        self.assertIn("progress off", text)
        self.assertIn("usage    off", text, "a role in the list places nothing")
        self.assertNotIn("角色", text)

    def test_an_unknown_placement_is_a_problem_not_agreement(self):
        write(os.path.join(self.inst, "plugins.json"), {"format": 1, "plugins": {"progress": {"on": "host-x"}}})
        code, text = self.run_show(["chatlog", "dcattach"])
        self.assertEqual(code, 1)
        self.assertIn("progress：不知道", text)

    def test_a_machine_not_on_the_list_is_an_error(self):
        self.run_show(["chatlog"])
        out = io.StringIO()
        self.assertEqual(plugins.show("nosuch", False, out), 1)
        self.assertIn("機器清單上沒有 nosuch", out.getvalue())

    def test_json(self):
        code, text = self.run_show(["chatlog"], json_out=True)
        d = json.loads(text)
        self.assertEqual(d["machine"], "host-b")
        self.assertEqual({p["name"]: p["placement"] for p in d["plugins"]}["chatlog"], "on")


class ListAll(unittest.TestCase):
    """plugins.py list: the core first, then by "order" (none: last, by name), each pitch and what it needs under it."""

    def test_order_pitch_and_needs(self):
        rows = [("core", "/e/altention", {"title": "核心", "machines": "all"}),
                ("zeta", "/e/zeta", {"title": "Z", "pitch": "做 Z 的事。", "requires": [{"plugin": "alpha"}]}),
                ("beta", "/e/beta", {"title": "B", "order": 20, "pitch": "做 B 的事。"}),
                ("alpha", "/e/alpha", {"title": "A", "order": 10, "machines": "one", "pitch": "做 A 的事。"}),
                ("eta", "/e/eta", {"title": "E"})]
        out = io.StringIO()
        with mock.patch.object(plugins.plugin, "discover", return_value=(rows, [])):
            self.assertEqual(plugins.list_all(out), 0)
        lines = out.getvalue().splitlines()
        self.assertEqual([l.split()[0] for l in lines if not l.startswith(" ")], ["core", "alpha", "beta", "eta", "zeta"])
        self.assertEqual(lines[1:3], ["alpha    one  A（/e/alpha）", "  做 A 的事。"])
        self.assertEqual(lines[-1], "  做 Z 的事。（需要：alpha）")
        self.assertIn("eta      all  E（/e/eta）", lines, "no pitch: the line alone")
        self.assertEqual(lines[lines.index("eta      all  E（/e/eta）") + 1][:4], "zeta")


class Questions(unittest.TestCase):
    """plugins.py questions: the install's questions, written here so Claude passes them on word for word."""

    ROWS = [("core", "/e/altention", {"title": "核心"}),
            ("newp", "/e/newp", {"title": "新的", "pitch": "做新的事。"}),
            ("progress", "/e/progress", {"title": "進度", "order": 20, "pitch": "整理進度。", "keep": "自動整理進度",
                                         "lose": "不會整理進度", "requires": [{"plugin": "chatlog", "data": "chatlog"}]}),
            ("chatlog", "/e/chatlog", {"title": "對話紀錄", "order": 10, "pitch": "存成文字檔。", "keep": "對話會存成文字檔",
                                       "lose": "對話不會存成文字檔"}),
            ("bare", "/e/bare", {"title": "光", "order": 30})]

    def by_name(self, rows=None):
        return {q["plugin"]: q for q in plugins.questions(rows or self.ROWS)}

    def test_order_without_core_and_no_order_last(self):
        self.assertEqual([q["plugin"] for q in plugins.questions(self.ROWS)], ["chatlog", "progress", "bare", "newp"])

    def test_a_need_is_said_with_titles(self):
        q = self.by_name()["progress"]
        self.assertEqual(q["header"], "進度")
        self.assertEqual(q["question"], "進度：整理進度。注意：要靠對話紀錄；保留進度，對話紀錄也會一起保留。"
                                        "要保留嗎？之後想開，跟 Claude 說一聲就好。")
        self.assertEqual(q["options"], [{"label": "保留", "description": "自動整理進度（對話紀錄也會一起保留）"},
                                        {"label": "關掉", "description": "少了進度，不會整理進度"}])
        self.assertNotIn("chatlog", json.dumps(q, ensure_ascii=False).replace('"plugin": "progress"', ""))

    def test_what_another_needs_says_so_when_turned_off(self):
        q = self.by_name()["chatlog"]
        self.assertEqual(q["options"][1]["description"], "少了對話紀錄，對話不會存成文字檔；進度也要靠它")
        self.assertNotIn("注意", q["question"])
        self.assertIs(q["multiSelect"], False)

    def test_no_pitch_keep_or_lose_still_a_question(self):
        q = self.by_name()["bare"]
        self.assertEqual(q["question"], "光。要保留嗎？之後想開，跟 Claude 說一聲就好。")
        self.assertEqual([o["description"] for o in q["options"]], ["光照常運作", "少了光"])

    def test_a_need_not_found_keeps_its_name(self):
        rows = [("p", "/e/p", {"title": "P", "pitch": "做 P。", "requires": [{"plugin": "gone"}]})]
        self.assertIn("注意：要靠gone；保留P，gone也會一起保留。", self.by_name(rows)["p"]["question"])

    def test_main_prints_json_and_text(self):
        with mock.patch.object(plugins.plugin, "discover", return_value=(self.ROWS, [])):
            out = io.StringIO()
            with mock.patch("sys.stdout", out):
                self.assertEqual(plugins.main(["questions", "--json"]), 0)
            self.assertEqual(json.loads(out.getvalue()), plugins.questions(self.ROWS))
            out = io.StringIO()
            with mock.patch("sys.stdout", out):
                self.assertEqual(plugins.main(["questions"]), 0)
            self.assertIn("  關掉：少了對話紀錄，對話不會存成文字檔；進度也要靠它\n", out.getvalue())
            with mock.patch("sys.stdout", io.StringIO()):
                self.assertEqual(plugins.main(["questions", "--bogus"]), 2)

    def test_first_names_every_part_by_title_in_order(self):
        q = plugins.first_question(self.ROWS)
        self.assertEqual(q["question"], "選用的功能（對話紀錄、進度、光、新的）要全部保留，還是關掉一些？")
        self.assertEqual(q["options"], [{"label": "全部保留（建議）", "description": "所有功能都裝上"},
                                        {"label": "關掉一些", "description": "接著一個一個問"}])
        self.assertIs(q["multiSelect"], False)
        self.assertNotIn("插件", json.dumps(q, ensure_ascii=False))
        self.assertEqual((q["key"], q["values"]), ("plugins", ["keep", "some"]))

    def test_setup_the_five_in_order(self):
        """#setup's order (C-48): time zone, permission, talk limit, update, the optional parts."""
        asked = plugins.setup_questions(self.ROWS, "Europe/Berlin", False)
        self.assertEqual([q["key"] for q in asked],
                         ["core.timezone", "dcbot.default_permission", "dcbot.talk_limit", "core.update", "plugins"])
        self.assertEqual(asked[4], plugins.first_question(self.ROWS))
        self.assertEqual(asked[0]["values"], ["Europe/Berlin", "UTC"])
        self.assertEqual(asked[0]["options"][0]["label"], "Europe/Berlin（建議）")
        self.assertEqual(asked[0]["options"][1], {"label": "UTC", "description": "世界標準時間"})
        self.assertIn("選「其他」", asked[0]["question"])
        utc = plugins.setup_questions(self.ROWS, "UTC", False)[0]
        self.assertEqual(utc["values"], ["UTC", "Asia/Taipei"])
        self.assertIn("也是這台機器目前的時區", utc["options"][0]["description"])
        unknown = plugins.setup_questions(self.ROWS, None, False)[0]
        self.assertEqual(unknown["values"], ["UTC", "Asia/Taipei"])
        self.assertNotIn("這台機器目前的時區", json.dumps(unknown, ensure_ascii=False), "nothing said it")
        self.assertEqual(asked[3]["values"], ["notify", "release"])
        self.assertEqual(asked[1]["values"], ["auto", "manual", "bypassPermissions"])
        self.assertEqual([o["label"] for o in asked[1]["options"]], ["auto（建議）", "manual", "bypassPermissions"],
                         "Claude Code's own names: its screen shows them")
        self.assertEqual([o["description"][:4] for o in asked[1]["options"]], ["自動判斷", "每次都問", "從不問："])
        self.assertEqual(asked[2]["values"], [10, 30, 0])
        for q in asked:
            self.assertEqual(len(q["values"]), len(q["options"]), q["key"])
            self.assertTrue(2 <= len(q["options"]) <= 4 and len(q["header"]) <= 12, q["key"])
            self.assertTrue(q["options"][0]["label"].endswith("（建議）"), q["key"])
            self.assertEqual(set(q), {"key", "values", "header", "question", "multiSelect", "options"})
            self.assertEqual([set(o) for o in q["options"]], [{"label", "description"}] * len(q["options"]))

    def test_setup_words_are_chinese_with_no_setting_names(self):
        """The person sees no setting key and no English word but names (Claude, Discord, the zones, the permission
        modes as Claude Code shows them)."""
        for unpacked, zone in ((False, "Europe/Berlin"), (True, None)):
            for q in plugins.setup_questions(self.ROWS, zone, unpacked):
                words = json.dumps([q["header"], q["question"], q["options"]], ensure_ascii=False)
                left = re.sub(r"Claude Code|Claude|Discord|Europe/Berlin|Asia/Taipei|UTC|bypassPermissions|manual|auto|label|description", "", words)
                self.assertNotRegex(left, "[A-Za-z]", q["key"])
                self.assertNotIn("插件", words)

    def test_setup_says_when_the_engine_never_updates(self):
        said = "這份引擎是從壓縮檔解開的，不管選哪個都不會自己更新，也不會通知新版；要新版請重新下載。"
        self.assertIn(said, plugins.setup_questions(self.ROWS, "UTC", True)[3]["question"])
        self.assertNotIn(said, plugins.setup_questions(self.ROWS, "UTC", False)[3]["question"])

    def test_setup_main_fills_this_machines_zone(self):
        with mock.patch.object(plugins.plugin, "discover", return_value=(self.ROWS, [])), \
                mock.patch("instance.system_timezone", return_value="Asia/Tokyo", create=True) as zone:
            import instance  # noqa: F401  (ops/ is on the path through plugins)
            out = io.StringIO()
            with mock.patch("sys.stdout", out):
                self.assertEqual(plugins.main(["questions", "--setup", "--json"]), 0)
        asked = json.loads(out.getvalue())
        self.assertEqual(asked[0]["values"][0], "Asia/Tokyo")
        zone.assert_called_once_with(None)
        self.assertEqual(len(asked), 5)

    def test_the_engines_own_plugins(self):
        found, problems = plugins.plugin.discover(plugins.plugin.ENGINE, instance="", root=tempfile.gettempdir())
        self.assertEqual(problems, [])
        asked = plugins.questions(found)
        self.assertNotIn("core", [q["plugin"] for q in asked])
        for q in asked:
            self.assertLessEqual(len(q["header"]), 12, q["plugin"])
            words = q["question"] + "".join(o["description"] for o in q["options"])
            self.assertEqual([n for n, _, _ in found if n in words], [], "a plugin's name in %s's words" % q["plugin"])
        first = plugins.first_question(found)
        self.assertEqual(first["question"], "選用的功能（%s）要全部保留，還是關掉一些？" % "、".join(q["header"] for q in asked))
        self.assertEqual([n for n, _, _ in found if n in first["question"]], [])
        progress = [q for q in asked if q["plugin"] == "progress"][0]
        self.assertIn("注意：要靠對話紀錄；保留進度，對話紀錄也會一起保留。", progress["question"])


    PLANNER = ("planner", "/e/planner", {"title": "秘書提醒", "order": 30, "pitch": "叫醒秘書。", "keep": "早報和提醒",
                                         "lose": "沒有早報"})

    def test_the_secretary_is_asked_with_the_setup_and_not_among_the_parts(self):
        """Owner 2026-10-16: the secretary was asked last, apart from the rest; now at the start, its channel opened
        later without asking again."""
        rows = self.ROWS + [self.PLANNER]
        asked = plugins.setup_questions(rows, "UTC", False)
        self.assertEqual([q["key"] for q in asked], ["core.timezone", "dcbot.default_permission", "dcbot.talk_limit",
                                                     "core.update", "plugins", "plugins.planner"])
        q = asked[-1]
        self.assertEqual((q["header"], q["values"]), ("秘書", ["on", "off"]))
        self.assertTrue(q["question"].startswith("秘書提醒：叫醒秘書。要開嗎？"))
        self.assertTrue(q["question"].endswith("要開嗎？開的話接著問時間。"), "nothing on how the install used to go")
        self.assertEqual([o["label"] for o in q["options"]], ["開（建議）", "不開"])
        self.assertEqual(q["options"][1]["description"], "少了秘書提醒，沒有早報")
        self.assertNotIn("planner", [x["plugin"] for x in plugins.questions(rows)])
        self.assertNotIn("秘書提醒", plugins.first_question(rows)["question"])
        self.assertEqual(len(plugins.setup_questions(self.ROWS, "UTC", False)), 5, "no planner: no such question")

    def test_the_secretarys_times_default_to_what_remind_writes(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("remind_q", os.path.join(plugins.plugin.ENGINE, "planner", "remind.py"))
        remind = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(remind)
        asked = plugins.briefing_questions()
        self.assertEqual([q["key"] for q in asked], ["planner.briefings.%s" % n for n, _, _ in remind.BRIEFINGS])
        self.assertEqual([q["values"][0] for q in asked], [w for _, w, _ in remind.BRIEFINGS], "the first is the default")
        for q in asked:
            self.assertTrue(q["options"][0]["label"].endswith("（建議）"), q["key"])
            self.assertLessEqual(len(q["header"]), 12)
            self.assertIn("其他", q["question"])
            self.assertEqual(len(q["values"]), len(q["options"]))
        out = io.StringIO()
        with mock.patch("sys.stdout", out):
            self.assertEqual(plugins.main(["questions", "--times", "--json"]), 0)
        self.assertEqual(json.loads(out.getvalue()), asked)
        with mock.patch("sys.stdout", io.StringIO()), mock.patch("sys.stderr", io.StringIO()):
            self.assertEqual(plugins.main(["questions", "--times", "--setup"]), 2)

    def test_the_backup_questions(self):
        """C-25 (decisions 1a, 2a, 4a): now or later; then which, the chat records offered and said to be verbatim."""
        import remote
        now, which = plugins.backup_questions()
        self.assertEqual((now["key"], now["values"], now["multiSelect"]), ("backup", ["now", "later"], False))
        self.assertEqual([o["label"] for o in now["options"]], ["現在設定（建議）", "之後再說"])
        self.assertIn("private", now["question"])
        self.assertIn("系統檢查會一直提醒", now["options"][1]["description"])
        self.assertEqual((which["key"], which["multiSelect"]), ("backup.items", True))
        self.assertEqual(which["values"], list(remote.NAMES), "ops/remote.py set's names, every one")
        self.assertEqual([o["label"] for o in which["options"]], [remote.LABELS[n] for n in remote.NAMES])
        self.assertIn("建議全部勾選", which["question"])
        self.assertIn("逐字", which["options"][1]["description"])
        for q in (now, which):
            self.assertLessEqual(len(q["header"]), 12)
            self.assertEqual(len(q["values"]), len(q["options"]))
        _, which = plugins.backup_questions(off={"chatlog", "planner"})
        self.assertEqual(which["values"], ["instance", "progress"], "a part turned off here is not offered")

    def test_main_prints_the_backup_questions(self):
        out = io.StringIO()
        with mock.patch.object(plugins, "off_here", return_value={"progress"}), mock.patch("sys.stdout", out):
            self.assertEqual(plugins.main(["questions", "--backup", "--json"]), 0)
        self.assertEqual(json.loads(out.getvalue()), plugins.backup_questions({"progress"}))
        with mock.patch("sys.stdout", io.StringIO()), mock.patch("sys.stderr", io.StringIO()):
            self.assertEqual(plugins.main(["questions", "--backup", "--setup"]), 2)


class RefreshReport(unittest.TestCase):
    """apply --write (install.sh) works this machine's report out again at once: doctor showed "會補 planner/…" from
    the quarter-hour report made before install.sh, and a Claude took it for "not installed" (2026-10-15)."""

    def setUp(self):
        self.home = tempfile.mkdtemp()
        self.env = mock.patch.dict(os.environ, {"HOME": self.home})
        self.env.start()
        from altention import cron_schedule
        cron_schedule.load()
        self.path = cron_schedule.report_paths("host-a")[0]
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, "w") as f:
            json.dump({"format": 1, "machine": "host-a", "at": 1, "add": [{"plugin": "planner", "name": "remind"}]}, f)

    def tearDown(self):
        self.env.stop()
        shutil.rmtree(self.home)

    def test_written_anew_after_apply(self):
        fresh = {"format": 1, "machine": "host-a", "add": [], "remove": [], "retime": []}
        with mock.patch.object(plugins.plugin, "this_machine", return_value="host-a"), \
                mock.patch.object(plugins, "report", return_value=dict(fresh)), mock.patch("sys.stdout", io.StringIO()):
            plugins.refresh_report(now=2000000000)
        with open(self.path) as f:
            self.assertEqual(json.load(f), dict(fresh, at=2000000000))

    def test_one_that_cannot_be_made_is_removed_not_left_stale(self):
        out = io.StringIO()
        with mock.patch.object(plugins.plugin, "this_machine", return_value="host-a"), \
                mock.patch.object(plugins, "report", side_effect=RuntimeError("x")), mock.patch("sys.stdout", out):
            plugins.refresh_report()
        self.assertFalse(os.path.exists(self.path))
        self.assertIn("套用報告沒有重算", out.getvalue())

    def test_main_apply_write_refreshes_even_when_apply_says_something(self):
        with mock.patch.object(plugins, "apply", return_value=1), mock.patch.object(plugins, "refresh_report") as refresh, \
                mock.patch("altention.engine_id.refusal", return_value=""), mock.patch("sys.stdout", io.StringIO()):
            plugins.main(["apply", "--write"])
        refresh.assert_called_once_with()
        with mock.patch.object(plugins, "apply", return_value=0), mock.patch.object(plugins, "refresh_report") as refresh, \
                mock.patch("sys.stdout", io.StringIO()):
            plugins.main(["apply"])
        refresh.assert_not_called()

if __name__ == "__main__":
    unittest.main()
