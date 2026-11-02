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
        self.assertIn("progress off      不在這台跑（放在別台）", text)
        self.assertIn("chatlog  on       開著", text)
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
        with mock.patch.object(plugins.plugin, "discover", return_value=(rows, [])), \
                mock.patch.object(plugins, "catalog_part"):  # the official list's part: CatalogAdd
            self.assertEqual(plugins.list_all(out), 0)
        lines = out.getvalue().splitlines()
        self.assertEqual([l.split()[0] for l in lines if not l.startswith(" ")], ["core", "alpha", "beta", "eta", "zeta"])
        self.assertEqual(lines[1:3], ["alpha    one  A（/e/alpha）", "  做 A 的事。"])
        self.assertEqual(lines[-1], "  做 Z 的事。（需要：alpha）")
        self.assertIn("eta      all  E（/e/eta）", lines, "no pitch: the line alone")
        self.assertEqual(lines[lines.index("eta      all  E（/e/eta）") + 1][:4], "zeta")

    def test_blocked_said_with_why_and_missing_uses_reminded_c94(self):
        rows = [("core", "/e/altention", {"title": "核心"}, "on", ""),
                ("demo", "/r/plugins/demo", {"title": "D"}, "blocked", "這版引擎沒有它要的介面（要 dc.post v2）"),
                ("rig", "/r/plugins/rig", {"title": "R"}, "on", ""),
                ("rigok", "/r/plugins/rigok", {"title": "R", "uses": {"dc.post": 1}}, "on", ""),
                ("rigd", "/r/plugins/rigd", {"title": "R"}, "draft", "草稿"),
                ("rigoff", "/r/plugins/rigoff", {"title": "R"}, "off", ""),
                ("rigdis", "/r/plugins/rigdis", {"title": "R"}, "disabled", "")]
        out = io.StringIO()
        with mock.patch.object(plugins.plugin, "discover", return_value=([r[:3] for r in rows], [])), \
                mock.patch.object(plugins.plugin, "on_this_machine", return_value=(rows, [])), \
                mock.patch.object(plugins.plugin, "external", side_effect=lambda f, root=None: f.startswith("/r/")), \
                mock.patch.object(plugins, "catalog_part"):
            self.assertEqual(plugins.list_all(out), 0)
        text = out.getvalue()
        self.assertIn("demo 沒有啟用：這版引擎沒有它要的介面（要 dc.post v2）\n", text)
        said = [l.split(" ")[0] for l in text.splitlines() if "沒寫 uses" in l]
        self.assertEqual(said, ["提醒：rig"], "on here and external, without uses: not core, a draft, off, disabled, blocked")

    def test_keys_the_engine_no_longer_reads_are_a_reminder_c95(self):
        rows = [("core", "/e/altention", {"title": "核心"}),
                ("old", "/e/old", {"title": "舊", "commands": [{"name": "x", "path": "x"}],
                                   "requires": [{"plugin": "core", "data": "x"}]}),
                ("new", "/e/new", {"title": "新", "requires": [{"plugin": "core"}]})]
        draft = ("rig", "/r/rig", {"title": "草", "commands": []})

        def discover(drafts):
            drafts.append(draft)
            return rows, []
        out = io.StringIO()
        with mock.patch.object(plugins.plugin, "discover", side_effect=discover), \
                mock.patch.object(plugins, "catalog_part"):
            self.assertEqual(plugins.list_all(out), 0, "a reminder, not a problem")
        said = [l for l in out.getvalue().splitlines() if "引擎已經不讀了" in l]
        self.assertEqual(said, ["提醒：old 的 plugin.json 還寫著 commands，引擎已經不讀了（沒有程式讀，刪掉）",
                                "提醒：old 的 plugin.json 還寫著 requires[].data，引擎已經不讀了（只看 plugin，刪掉 data）",
                                "提醒：rig 的 plugin.json 還寫著 commands，引擎已經不讀了（沒有程式讀，刪掉）"])


class Questions(unittest.TestCase):
    """plugins.py questions: the install's questions, written here so Claude passes them on word for word."""

    ROWS = [("core", "/e/altention", {"title": "核心"}),
            ("newp", "/e/newp", {"title": "新的", "pitch": "做新的事。"}),
            ("progress", "/e/progress", {"title": "進度", "order": 20, "pitch": "整理進度。", "keep": "自動整理進度",
                                         "lose": "不會整理進度", "requires": [{"plugin": "chatlog"}]}),
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


class BackupQuestions(unittest.TestCase):
    def test_an_external_plugins_private_data_is_offered(self):
        """C-61 E4: the data an external plugin on here calls private is among the backups, by its remote.py name."""
        import remote
        plain = plugins.backup_questions()
        self.assertIn("進度和秘書的計畫現在都只存在這台電腦", plain[0]["question"])
        with mock.patch.object(remote, "plugin_data", return_value=[("plugin:demo/notes", "/d/demo", True, "demo")]):
            q = plugins.backup_questions()
        self.assertIn("plugin:demo/notes", q[1]["values"])
        self.assertIn("插件 demo 的「notes」", [o["label"] for o in q[1]["options"]])
        self.assertIn("秘書的計畫和插件的資料現在都只存在這台電腦", q[0]["question"])

def repo_url(name):
    """A plugin's address in the official list, put together (this file names no account)."""
    from altention import catalog
    return catalog.repo_url(name)


class CatalogAdd(unittest.TestCase):
    """C-81 K2: plugins.py list's 「可以裝的」 and add, from a kept official list; the instance a git repo of its own."""
    def setUp(self):
        import subprocess
        from altention import catalog
        self.catalog = catalog
        self.home = tempfile.mkdtemp(prefix="家 catalog ")
        self.addCleanup(shutil.rmtree, self.home, True)
        self.inst = os.path.join(self.home, "實例")
        env = mock.patch.dict(os.environ, {"HOME": self.home, "ALT_INSTANCE": self.inst,
                                           "ALT_ROOT": os.path.join(self.home, "altention")})
        env.start()
        self.addCleanup(env.stop)
        write(os.path.join(self.inst, "plugins.json"), {"format": 1, "plugins": {"mine": {"local": True}}})
        write(os.path.join(self.inst, "machines.json"), {"format": 1, "machines": {
            "box": {"ssh": ["local"], "roles": ["dcbot"]}, "far": {"ssh": ["far"], "roles": []}}})
        write(os.path.join(self.home, ".config", "altention", "host"), "box\n")
        self.git = lambda *a: subprocess.run(["git", "-C", self.inst, "-c", "user.name=t", "-c", "user.email=t@example.org"]
                                             + list(a), check=True, stdout=subprocess.PIPE, universal_newlines=True).stdout
        self.git("init", "-q")
        self.git("add", "-A")
        self.git("commit", "-qm", "start")
        self.pushed = []
        from altention import update_go
        for p in (mock.patch.object(update_go, "push_instance", side_effect=lambda: self.pushed.append(1) or ""),
                  mock.patch.object(plugins, "engine_release", return_value="v0.5.0")):
            p.start()
            self.addCleanup(p.stop)

    def keep(self, plugins_=None, left_out=()):
        draw = {"repo": repo_url("draw"), "version": "v0.3.0", "points": [],
                "plugin": {"format": 1, "name": "draw", "title": "畫圖", "description": "d", "pitch": "把公式畫成圖。",
                           "needs": [{"name": "chrome", "check": "true", "hint": "h"}],
                           "schedules": [{"name": "clean", "when": {"every": "1h"}, "do": {"run": "x"}}]}}
        hooky = {"repo": repo_url("hooky"), "version": "v1.0.0", "points": [],
                 "plugin": {"format": 1, "name": "hooky", "title": "掛勾", "description": "d",
                            "hooks": [{"event": "Stop", "command": "{plugin}/h.sh"}]}}
        mine = {"repo": repo_url("mine"), "version": "v0.1.0", "points": [],
                "plugin": {"format": 1, "name": "mine", "title": "我的", "description": "d"}}
        d = {"format": 1, "at": __import__("time").time(), "tried": 0, "left_out": list(left_out),
             "plugins": plugins_ if plugins_ is not None else {"draw": draw, "hooky": hooky, "mine": mine}}
        write(self.catalog.path(), d)

    def run_(self, *argv, again=None, apply_cmd=None):
        out = io.StringIO()
        with mock.patch("sys.stdout", out):
            code = plugins.main(list(argv), again=again, apply_cmd=apply_cmd)
        return code, out.getvalue()

    def instance(self):
        with open(os.path.join(self.inst, "plugins.json"), encoding="utf-8") as f:
            return json.load(f)

    def test_an_empty_list_says_none_yet_not_a_fault(self):
        """Before the first plugin is published the list is read and empty: that is not "not fetched"."""
        write(self.catalog.path(), {"format": self.catalog.FORMAT, "at": 1000000, "plugins": {}})
        code, out = self.run_("list")
        self.assertEqual(code, 0)
        self.assertIn("）：目前還沒有可以裝的官方插件\n", out)
        self.assertNotIn("官方插件清單還沒抓到", out)
        code, out = self.run_("add", "draw")
        self.assertEqual(code, 1)
        self.assertIn("官方清單沒有 draw（目前還沒有可以裝的官方插件）", out)

    def test_listed_what_it_would_do_not_what_the_instance_names(self):
        code, out = self.run_("list")
        self.assertIn("官方插件清單還沒抓到", out)
        self.keep(left_out=[{"repo": "plugin-bad", "version": "v0.1.0", "why": "讀不懂"}])
        code, out = self.run_("list")
        part = out[out.index("可以裝的"):]
        self.assertIn("draw     v0.3.0  畫圖：把公式畫成圖。", part)
        self.assertIn("新的排程 clean：每 1h", part)
        self.assertIn("要這些系統工具（不會自己裝）：chrome", part)
        self.assertIn("新的 hook：Stop（它會看到你所有 Claude 對話的內容）", part)
        self.assertIn("裝：到 #setup 跟 Claude 說「裝 draw」", part)
        self.assertNotIn("mine ", part, "the instance names it already")
        self.assertNotIn("plugin-bad", part)
        self.assertIn("沒收：plugin-bad v0.1.0：讀不懂", self.run_("list", "--catalog")[1])

    def test_told_first_written_only_on_yes(self):
        self.keep()
        before = self.git("rev-parse", "HEAD")
        code, out = self.run_("add", "draw", again="python3 /e/ops/setup.py plugin-add")
        self.assertEqual(code, 0, out)
        self.assertIn("官方插件 畫圖（draw v0.3.0）", out)
        self.assertIn("  - 新的排程 clean：每 1h", out)
        self.assertIn("要裝：python3 /e/ops/setup.py plugin-add draw v0.3.0 --yes\n", out)
        self.assertNotIn("draw", self.instance()["plugins"])
        self.assertEqual(self.git("rev-parse", "HEAD"), before)
        code, out = self.run_("add", "draw", "--yes")
        self.assertEqual(code, 0, out)
        self.assertEqual(self.instance()["plugins"]["draw"],
                         {"source": repo_url("draw"), "version": "v0.3.0"})
        self.assertEqual(self.git("log", "-1", "--format=%s").strip(), "plugins: add draw v0.3.0")
        self.assertEqual(self.git("status", "--porcelain"), "")
        self.assertEqual(self.pushed, [1])
        self.assertEqual(self.run_("add", "draw", "--yes")[0], 1, "named now: not again")

    def test_hooks_asked_on_their_own(self):
        self.keep()
        code, out = self.run_("add", "hooky")
        self.assertIn("它會看到你的對話內容（hook Stop）：這一點要另外問過", out)
        self.assertIn("hooky v1.0.0 --yes --hooks-ok", out)
        code, out = self.run_("add", "hooky", "--yes")
        self.assertEqual(code, 1, out)
        self.assertNotIn("hooky", self.instance()["plugins"])
        self.assertEqual(self.run_("add", "hooky", "--hooks-ok")[0], 2, "--hooks-ok alone is not a yes")
        self.assertEqual(self.run_("add", "hooky", "--yes", "--hooks-ok")[0], 0)
        self.assertEqual(self.instance()["plugins"]["hooky"]["version"], "v1.0.0")

    def test_a_rewrite_and_a_skill_are_said_and_the_rewrite_asked(self):
        self.keep()
        d = json.load(open(self.catalog.path()))
        base = d["plugins"]["mine"]
        for n, more in (("rw", {"rewrite": {"command": "{plugin}/rw", "when": "."}}),
                        ("sk", {"skills": [{"name": "sk-help", "path": "skill"}]}),
                        ("both", {"rewrite": {"command": "{plugin}/rw", "when": "."}, "skills": [{"name": "b", "path": "s"}],
                                  "hooks": [{"event": "Stop", "command": "{plugin}/h"}]})):
            d["plugins"][n] = dict(base, repo=repo_url(n), plugin=dict(base["plugin"], name=n, **more))
        write(self.catalog.path(), d)
        listed = self.run_("list")[1]
        self.assertIn("新的改寫回覆：{plugin}/rw（每一則 Claude 的回覆貼出前都先交給它，它會看到你的對話內容）", listed)
        self.assertIn("所有 Claude 都會讀到它的 skill：sk-help", listed)
        self.assertNotIn("不會在這台多做什麼", listed.split("可以裝的")[1].split("  sk ")[0].split("  rw ")[-1])
        code, out = self.run_("add", "rw")
        self.assertIn("它會看到你的對話內容（改寫回覆）", out)
        self.assertIn("rw v0.1.0 --yes --hooks-ok", out)
        self.assertEqual(self.run_("add", "rw", "--yes")[0], 1, "a rewrite alone: asked on its own too")
        code, out = self.run_("add", "sk")
        self.assertIn("  - 所有 Claude 都會讀到它的 skill：sk-help", out)
        self.assertNotIn("對話內容", out, "a skill is said, not asked again")
        self.assertIn("sk v0.1.0 --yes\n", out)
        self.assertEqual(self.run_("add", "sk", "--yes")[0], 0)
        code, out = self.run_("add", "both")
        self.assertIn("它會看到你的對話內容（hook Stop、改寫回覆）", out)
        self.assertEqual(self.run_("add", "both", "--yes")[0], 1)
        self.assertEqual(self.run_("add", "both", "--yes", "--hooks-ok")[0], 0)
        plain = dict(base["plugin"], name="plain")
        self.assertEqual(plugins.does_here(plain), [])

    def test_refused(self):
        self.assertEqual(self.run_("add", "draw")[0], 1, "no list kept")
        self.keep()
        code, out = self.run_("add", "nothere")
        self.assertEqual(code, 1)
        self.assertIn("官方清單沒有 nothere（清單裡有：draw、hooky、mine）", out)
        code, out = self.run_("add", "mine", "--yes")
        self.assertEqual(code, 1)
        self.assertEqual(self.instance()["plugins"]["mine"], {"local": True}, "the instance's own entry untouched")
        with mock.patch.object(plugins, "engine_release", return_value="v0.4.0"):
            d = json.load(open(self.catalog.path()))
            d["plugins"]["draw"]["plugin"]["engine"] = ">=0.5.0"
            write(self.catalog.path(), d)
            code, out = self.run_("add", "draw", "--yes")
            self.assertEqual(code, 1)
            self.assertIn("要引擎 >=0.5.0，這台是 v0.4.0", out)
            self.assertIn("要引擎 >=0.5.0，這台是 v0.4.0：先更新引擎", self.run_("list")[1])
        self.assertNotIn("draw", self.instance()["plugins"])

    def test_an_older_version_is_read_from_its_tag(self):
        self.keep()
        older = {"repo": repo_url("draw"), "version": "v0.2.0", "points": [],
                 "plugin": {"format": 1, "name": "draw", "title": "畫圖", "description": "d"}}
        with mock.patch.object(self.catalog, "tags", return_value=["v0.3.0", "v0.2.0"]), \
                mock.patch.object(self.catalog, "entry", return_value=(older, None)) as entry:
            code, out = self.run_("add", "draw", "v0.1.0")
            self.assertEqual(code, 1)
            self.assertIn("沒有 v0.1.0 這一版（有：v0.3.0、v0.2.0）", out)
            code, out = self.run_("add", "draw", "v0.2.0")
            self.assertEqual(code, 0, out)
            self.assertIn("不會在這台多做什麼", out)
            entry.assert_called_with("draw", "v0.2.0")
            self.assertEqual(self.run_("add", "draw", "v0.2.0", "--yes")[0], 0)
        self.assertEqual(self.instance()["plugins"]["draw"]["version"], "v0.2.0")
        self.assertEqual(self.run_("add", "draw", "0.2")[0], 1, "not vX.Y.Z")

    def test_setup_runs_it_and_then_install(self):
        import setup
        self.keep()
        ran = []

        class Done:
            returncode, stdout = 0, "…\ninstall 完成\n"
        with mock.patch.object(setup, "script", side_effect=lambda argv, **k: ran.append(argv) or Done()):
            out = io.StringIO()
            with mock.patch("sys.stdout", out):
                self.assertEqual(setup.main(["plugin-add", "draw"]), 0)
            self.assertIn("setup.py plugin-add draw v0.3.0 --yes", out.getvalue())
            self.assertEqual(ran, [], "told only: install not run")
            with mock.patch("sys.stdout", io.StringIO()) as o:
                self.assertEqual(setup.main(["plugin-add", "draw", "v0.3.0", "--yes"]), 0, o.getvalue())
            self.assertEqual(ran, [[os.path.join(setup.ENGINE, "install.sh")]])
            Done.returncode = 1
            with mock.patch("sys.stdout", io.StringIO()) as o:
                self.assertEqual(setup.main(["plugin-add", "mine2"]), 1)
        self.assertEqual(len(ran), 1)

    def test_the_other_machines_are_named_never_done(self):
        """manager 2026-10-21: written into the instance only; the machines it runs on besides this one are named,
        with the command that brings each in line now, asked in #setup."""
        self.keep()
        code, out = self.run_("add", "draw", "--yes")
        self.assertEqual(code, 0, out)
        self.assertIn("別台用到它的：far。", out)
        self.assertIn("ops/apply.py far", out)
        d = json.load(open(self.catalog.path()))
        d["plugins"]["mine2"] = dict(d["plugins"]["mine"], plugin=dict(d["plugins"]["mine"]["plugin"], name="mine2",
                                                                       machines="one"))
        write(self.catalog.path(), d)
        code, out = self.run_("add", "mine2", "--yes", again="python3 /e/ops/setup.py plugin-add",
                              apply_cmd="python3 /e/ops/setup.py plugin-apply")
        self.assertEqual(code, 0, out)
        self.assertNotIn("別台用到它的", out, "one machine, the bot's, and this is it")
        with mock.patch.object(plugins.config, "this_machine", return_value="far"):
            self.assertEqual(plugins.users_elsewhere({"machines": "one"}), ["box"])
            self.assertEqual(plugins.users_elsewhere({}), ["box"])

    def test_setup_brings_another_machine_in_line_only_for_a_plugin_that_runs_there(self):
        """manager 2026-10-21: plugin-apply PLUGIN MACHINE only for an external plugin the instance names that runs on
        that other machine; anything else is wrong usage and nothing runs."""
        import setup
        self.keep()
        ran = []

        class R:
            returncode, stdout = 0, "…\nfar：好了\n"

        def call(*argv):
            with mock.patch("sys.stdout", io.StringIO()) as o:
                code = setup.main(list(argv))
            return code, o.getvalue()
        with mock.patch.object(setup, "script", side_effect=lambda argv, **k: ran.append(argv) or R()):
            code, out = call("plugin-apply", "draw", "far")
            self.assertEqual((code, ran), (2, []), "not in the instance yet")
            self.assertIn("實例沒有外部插件 draw", out)
            code, out = self.run_("add", "draw", "--yes", apply_cmd="python3 /e/ops/setup.py plugin-apply")
            self.assertEqual(code, 0, out)
            self.assertIn("要現在裝：python3 /e/ops/setup.py plugin-apply draw far", out)
            for argv in (("plugin-apply", "draw", "box"), ("plugin-apply", "draw", "nowhere"), ("plugin-apply", "mine", "far"),
                         ("plugin-apply", "far"), ("plugin-apply", "draw", "far", "x")):
                code, out = call(*argv)
                self.assertEqual(code, 2, argv)
            self.assertEqual(ran, [], "nothing run for any of those")
            code, out = call("plugin-apply", "draw", "far")
            self.assertEqual(code, 0, out)
            self.assertEqual([a[1:] for a in ran], [[os.path.join(setup.ENGINE, "ops", "apply.py"), "far"],
                                                    [os.path.join(setup.ENGINE, "ops", "doctor.py")]])
            self.assertIn("apply far：… ｜ far：好了", out)
            R.returncode = 1
            self.assertEqual(call("plugin-apply", "draw", "far")[0], 2)
            d = self.instance()
            d["plugins"]["draw"]["disabled_on"] = ["far"]
            write(os.path.join(self.inst, "plugins.json"), d)
            del ran[:]
            code, out = call("plugin-apply", "draw", "far")
            self.assertEqual((code, ran), (2, []), "turned off there")
            self.assertIn("draw 用到的別台：沒有", out)

    def test_where_a_plugin_runs(self):
        self.assertEqual(plugins.machines_for("x", {}, {"machines": "all"}), ["box", "far"])
        self.assertEqual(plugins.machines_for("x", {"disabled_on": ["far"]}, {}), ["box"])
        self.assertEqual(plugins.machines_for("x", {}, {"machines": "one"}), ["box"], "the bot's")
        self.assertEqual(plugins.machines_for("x", {"on": "far"}, {"machines": "one"}), ["far"])
        self.assertEqual(plugins.users_elsewhere({"machines": "all"}), ["far"])

    def test_declare_never_changes_an_entry(self):
        from altention import plugin_update
        ok, why = plugin_update.declare("mine", "https://example.org/x.git", "v1.0.0")
        self.assertFalse(ok)
        self.assertIn("實例已經寫了插件 mine", why)
        self.assertEqual(self.instance()["plugins"]["mine"], {"local": True})



class Announce(unittest.TestCase):
    """C-85 (c): a plugin turned off or on here is said once in its channels, naming the machine (manager 2026-10-21:
    only when it changed, which machine, the others still running it, never failing apply)."""
    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="家 announce ")
        self.addCleanup(shutil.rmtree, self.home, True)
        env = mock.patch.dict(os.environ, {"HOME": self.home})
        env.start()
        self.addCleanup(env.stop)
        self.sent, self.ok = [], True
        settings = {"usage": {"channel": "111"}, "demo": {"channel": "222"}}
        for p in (mock.patch.object(plugins, "notify", side_effect=lambda c, t: self.sent.append((c, t)) or self.ok),
                  mock.patch.object(plugins.config, "setting", side_effect=lambda k, default=None: (
                      settings.get(k.split(".")[0], {}).get(k.split(".")[1], default))),
                  mock.patch.object(plugins.config, "machines", return_value={"host-a": {}, "host-b": {}})):
            p.start()
            self.addCleanup(p.stop)
        self.usage = {"title": "用量統計", "machines": "one", "channels": [{"name": "dashboard", "kind": "board", "setting": "usage.channel"}]}
        self.demo = {"title": "示範頻道", "machines": "all", "channels": [{"name": "demo", "kind": "claude", "setting": "demo.channel"}]}
        self.quiet = {"title": "沒頻道", "machines": "all"}

    def rows(self, usage="on", demo="on", quiet="on"):
        return [("usage", "/e/usage", self.usage, usage, ""), ("demo", "/p/demo", self.demo, demo, ""),
                ("quiet", "/e/quiet", self.quiet, quiet, "")]

    def run_(self, rows, elsewhere="on"):
        out = io.StringIO()
        with mock.patch.object(plugins.plugin, "on_this_machine", return_value=(
                [("demo", "/p/demo", self.demo, elsewhere, "")], [])):
            plugins.announce(rows, "host-a", {}, out)
        return out.getvalue()

    def test_first_apply_notes_without_saying_then_each_change_once(self):
        self.run_(self.rows(usage="disabled"))
        self.assertEqual(self.sent, [], "nothing noted before: nothing to compare with")
        self.run_(self.rows(usage="disabled"))
        self.assertEqual(self.sent, [])
        out = self.run_(self.rows(usage="on"))
        self.assertEqual(self.sent, [("111", "用量統計 在 host-a 打開了，這裡會再開始更新。")])
        self.assertIn("說了", out)
        self.run_(self.rows(usage="on"))
        self.assertEqual(len(self.sent), 1, "the same again: said once only")
        self.run_(self.rows(usage="disabled", quiet="disabled"))
        self.assertEqual(self.sent[1:], [("111", "用量統計 在 host-a 關掉了，這裡不會再更新。"),
                                         ("@notify", "沒頻道 在 host-a 關掉了。")], "no channel: the notification channel")

    def test_a_plugin_with_no_channel_set_is_said_in_the_notification_channel(self):
        """L3's off/on run: a plugin that declares no channel (a rewrite of every reply, say), or one not set in the
        instance, was never said; now in @notify, in words without 「這裡」."""
        self.demo["channels"][0]["setting"] = "nosuch.channel"
        self.run_(self.rows())
        self.run_(self.rows(quiet="disabled", demo="disabled"))
        self.assertEqual(self.sent, [("@notify", "示範頻道 在 host-a 關掉了；host-b 照常。"),
                                     ("@notify", "沒頻道 在 host-a 關掉了。")])
        self.run_(self.rows())
        self.assertEqual(self.sent[2:], [("@notify", "示範頻道 在 host-a 打開了。"), ("@notify", "沒頻道 在 host-a 打開了。")])

    def test_a_plugin_on_every_machine_names_the_others_still_running_it(self):
        self.run_(self.rows())
        self.run_(self.rows(demo="disabled"))
        self.assertEqual(self.sent, [("222", "示範頻道 在 host-a 關掉了，它在 host-a 的部分不會再更新；host-b 照常。")])
        self.run_(self.rows(demo="on"))
        self.run_(self.rows(demo="draft"), elsewhere="draft")
        self.assertEqual(self.sent[-1], ("222", "示範頻道 在 host-a 變成草稿了（實例沒寫它），這裡不會再更新。"))
        self.run_(self.rows(demo="off", usage="off"))
        self.assertEqual(len(self.sent), 4, "off after draft: still not on, nothing to say")

    def test_one_not_said_is_said_at_the_next_apply_and_apply_goes_on(self):
        self.run_(self.rows())
        self.ok = False
        out = self.run_(self.rows(usage="disabled"))
        self.assertIn("沒說出去，下次 apply 再說", out)
        self.ok = True
        self.run_(self.rows(usage="disabled"))
        self.assertEqual([t for _c, t in self.sent], ["用量統計 在 host-a 關掉了，這裡不會再更新。"] * 2)
        self.run_(self.rows(usage="disabled"))
        self.assertEqual(len(self.sent), 2)

    def test_unknown_this_time_keeps_what_it_was(self):
        self.run_(self.rows())
        self.run_(self.rows(usage="unknown"))
        self.run_(self.rows())
        self.assertEqual(self.sent, [], "unknown then on again: never was anything else")


class Notify(unittest.TestCase):
    def test_dc_notify_with_a_time_limit_never_raising(self):
        import subprocess as sp
        with mock.patch.object(plugins.subprocess, "run", return_value=sp.CompletedProcess([], 0)) as run:
            self.assertTrue(plugins.notify("111", "說一句"))
        argv = run.call_args.args[0]
        self.assertEqual(argv[1:], ["dc", "notify", "-c", "111", "說一句"])
        self.assertEqual(run.call_args.kwargs["timeout"], 60)
        for e in (OSError("gone"), sp.TimeoutExpired("x", 60)):
            with mock.patch.object(plugins.subprocess, "run", side_effect=e):
                self.assertFalse(plugins.notify("111", "x"))
        with mock.patch.object(plugins.subprocess, "run", return_value=sp.CompletedProcess([], 1)):
            self.assertFalse(plugins.notify("111", "x"))

class Lose(unittest.TestCase):
    """C-85 (e): what turning a plugin off loses, for Claude to say before it asks."""
    ROWS = [("core", "/e/altention", {"title": "核心"}),
            ("logs", "/e/logs", {"title": "紀錄", "lose": "對話不會存下來",
                                 "hooks": [{"event": "Stop", "command": "x"}, {"event": "PreToolUse", "matcher": "Bash", "command": "y"}],
                                 "schedules": [{"name": "sync", "when": {"every": "15m"}, "do": {"run": "z"}}]}),
            ("digest", "/e/digest", {"title": "整理", "requires": [{"plugin": "logs"}],
                                     "channels": [{"name": "board", "kind": "board", "setting": "digest.channel"}]})]

    def run_(self, name):
        out = io.StringIO()
        with mock.patch.object(plugins.plugin, "discover", return_value=(self.ROWS, [])), \
                mock.patch.object(plugins.config, "settings", return_value={}):
            code = plugins.lose(name, out)
        return code, out.getvalue()

    def test_what_goes_with_it(self):
        code, text = self.run_("logs")
        self.assertEqual(code, 0)
        self.assertEqual(text.splitlines(), [
            "關掉紀錄（logs）會少掉：對話不會存下來",
            "要靠它的也會停：整理",
            "停掉的排程：sync（每 15m（plugin.json））",
            "拿掉的 hook：Stop、PreToolUse（Bash）（它不再看到 Claude 的對話）",
            "它的資料留著，再打開就接著用。"])
        code, text = self.run_("digest")
        self.assertIn("關掉整理（digest）會少掉：（plugin.json 沒寫）", text)
        self.assertIn("不再更新的頻道：#board", text)
        self.assertNotIn("要靠它的", text)

    def test_the_core_and_a_name_not_there(self):
        self.assertEqual(self.run_("core"), (1, "核心不能關。\n"))
        code, text = self.run_("nope")
        self.assertEqual(code, 1)
        self.assertIn("沒有叫 nope 的插件（有：digest、logs）", text)

if __name__ == "__main__":
    unittest.main()
