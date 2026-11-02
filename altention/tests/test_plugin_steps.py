"""altention.plugin_steps (C-77 G1): a plugin's plan here, its checks' exit statuses (0, 3, other), the kept
answers, and plugin.json's needs[]/guide[]/offer fields; in a throwaway home."""
import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))))
from altention import testing as _testing  # noqa: E402
_testing.guard_home()  # tests never touch the real home (altention/testing.py)
import json
import os
import shutil
import tempfile
import time
import unittest
from unittest import mock

from altention import plugin, plugin_steps as ps

BASE = {"format": 1, "name": "demo", "title": "D", "description": "d", "machines": "all",
        "secrets": [{"name": "rig-token", "path": "~/.config/altention/secrets/demo/rig-token"}]}


def shaped(**more):
    d = dict(BASE, **more)
    return plugin.check(json.loads(json.dumps(d)))


class Rig(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="家 g1 ")
        self.addCleanup(shutil.rmtree, self.home, True)
        self.folder = os.path.join(self.home, "插件 demo")
        os.makedirs(self.folder)
        env = mock.patch.dict(os.environ, {"HOME": self.home})
        env.start()
        self.addCleanup(env.stop)

    def flag(self, name, code):
        """A check that exits `code` while the file <folder>/<name> says so."""
        with open(os.path.join(self.folder, name), "w") as f:
            f.write(str(code))
        return 'echo "why %s"; exit $(cat "{plugin}/%s")' % (name, name)


class ExitStatuses(Rig):
    def test_zero_done_three_asked_other_not_yet_with_its_reason(self):
        d = shaped(guide=[{"id": "a", "title": "A", "check": self.flag("a", 0)},
                          {"id": "b", "title": "B", "check": self.flag("b", 3), "ask": "B 好了嗎？"},
                          {"id": "c", "title": "C", "check": self.flag("c", 1)}])
        p = ps.plan("demo", self.folder, d)
        st = {s["id"]: s for s in p["guide"]}
        self.assertEqual([st[x]["state"] for x in "abc"], ["ok", "ask", "todo"])
        self.assertEqual(st["b"]["ask"], "B 好了嗎？")
        self.assertEqual(st["b"]["reason"], "", "a step that cannot be checked here is asked, not called failed")
        self.assertEqual(st["c"]["reason"], "why c")
        self.assertEqual(p["next"], "b")
        self.assertEqual(p["left"], 2)
        self.assertEqual(p["failing"], ["c"], "only what a check said is not done")

    def test_three_answered_counts_like_a_null_check_and_zero_later_wins(self):
        d = shaped(guide=[{"id": "b", "title": "B", "check": self.flag("b", 3)},
                          {"id": "n", "title": "N", "check": None, "ask": "N？"}])
        ps.said("demo", "b", "yes")
        ps.said("demo", "n", "yes")
        p = ps.plan("demo", self.folder, d)
        self.assertEqual([s["state"] for s in p["guide"]], ["ok", "ok"])
        self.assertEqual(p["left"], 0)
        self.flag("b", 1)  # the check can tell now, and says not yet: the kept yes does not stand
        p = ps.plan("demo", self.folder, d)
        self.assertEqual(p["guide"][0]["state"], "todo")
        self.assertEqual(ps.lines(p)[-1], "還有 1 件事要做，下一件是「B」（b）。")

    def test_three_without_an_ask_gets_a_plain_question(self):
        d = shaped(guide=[{"id": "b", "title": "開後台", "check": self.flag("b", 3)}])
        self.assertEqual(ps.plan("demo", self.folder, d)["guide"][0]["ask"], "「開後台」做好了嗎？")

    def test_needs_three_asked_answered_and_optional_declined(self):
        d = shaped(needs=[{"name": "tool", "check": self.flag("t", 3), "ask": "tool 裝了嗎？"},
                          {"name": "pdf", "check": "exit 1", "optional": True, "for": ["pdf"],
                           "install": {"debian": "sudo apt install -y x", "macos": "brew install x"}},
                          {"name": "gone", "check": self.flag("g", 3), "optional": True}])
        with mock.patch.object(ps, "os_name", return_value="debian"):
            p = ps.plan("demo", self.folder, d)
        n = {x["name"]: x for x in p["needs"]}
        self.assertEqual((n["tool"]["state"], n["pdf"]["state"], n["gone"]["state"]), ("ask", "todo", "ask"))
        self.assertTrue(n["pdf"]["sudo"])
        self.assertEqual(n["gone"]["ask"], "這台電腦裝好 gone 了嗎？")
        self.assertEqual(p["level"], 2)
        self.assertEqual((p["left"], p["offers"]), (1, 2), "optional ones never hold the plugin back")
        ps.said("demo", "needs:tool", "yes")
        ps.said("demo", "needs:gone", "no")
        p = ps.plan("demo", self.folder, d)
        self.assertEqual([x["state"] for x in p["needs"]], ["ok", "todo", "declined"])
        self.assertEqual(p["left"], 0)

    def test_a_no_to_a_step_that_is_not_optional_stays_to_ask(self):
        d = shaped(guide=[{"id": "n", "title": "N", "check": None, "ask": "N？"}])
        ps.said("demo", "n", "no")
        self.assertEqual(ps.plan("demo", self.folder, d)["guide"][0]["state"], "ask")

    def test_timeout_cannot_start_and_no_time_left_are_not_done(self):
        d = shaped(guide=[{"id": "slow", "title": "S", "check": "sleep 5"},
                          {"id": "set", "title": "T", "check": "{setting:rig.unset}"},
                          {"id": "late", "title": "L", "check": "true"}])
        with mock.patch.object(ps, "CHECK_TIMEOUT", 1):
            p = ps.plan("demo", self.folder, d, settings={}, deadline=time.time() + 2.5)
        st = [s["state"] for s in p["guide"]]
        self.assertEqual(st[:2], ["todo", "todo"])
        self.assertIn("秒沒有結束", p["guide"][0]["reason"])
        self.assertIn("檢查指令沒辦法跑", p["guide"][1]["reason"])
        p = ps.plan("demo", self.folder, d, deadline=time.time())
        self.assertEqual({s["state"] for s in p["guide"]}, {"unchecked"})

    def test_next_waits_for_after_and_skips_optional(self):
        d = shaped(guide=[{"id": "a", "title": "A", "check": "false"},
                          {"id": "opt", "title": "O", "check": None, "ask": "要嗎？", "optional": True},
                          {"id": "b", "title": "B", "check": "false", "after": ["opt"]}])
        p = ps.plan("demo", self.folder, d)
        self.assertEqual(p["next"], "a")
        self.assertEqual(p["guide"][1]["after"], ["a"], "by default a step waits for the ones before it")
        self.assertEqual(p["guide"][2]["after"], ["opt"])
        d["guide"][0]["check"] = "true"
        self.assertEqual(ps.plan("demo", self.folder, d)["next"], "opt")
        ps.said("demo", "opt", "no")
        p = ps.plan("demo", self.folder, d)
        self.assertEqual((p["next"], p["left"], p["offers"]), ("b", 1, 0))
        d["guide"][2]["check"] = "true"
        self.assertEqual(ps.lines(ps.plan("demo", self.folder, d))[-1], "都好了。")
        os.remove(ps.answers_path("demo"))
        self.assertEqual(ps.lines(ps.plan("demo", self.folder, d))[-1], "都好了（另有 1 項可選的，還沒決定要不要）。")

    def test_answers_file_unreadable_is_no_answers(self):
        os.makedirs(os.path.dirname(ps.answers_path("demo")))
        with open(ps.answers_path("demo"), "w") as f:
            f.write("{")
        self.assertEqual(ps.answers("demo"), {})
        ps.said("demo", "a", "yes")
        self.assertEqual(ps.answers("demo")["a"]["answer"], "yes")
        self.assertTrue(ps.answers_path("demo").startswith(self.home), "kept under HOME, read at call time")

    def test_lines_and_levels(self):
        d = shaped(setup=[{"name": "tools", "run": "true"}])
        p = ps.plan("demo", self.folder, d)
        self.assertEqual((p["level"], p["left"]), (1, 0))
        self.assertEqual(ps.lines(p)[-1], "都好了。")
        self.assertEqual(ps.lines(p)[0], "demo 在這台的安裝計畫（第 1 級：安裝時自動裝好）")
        d = shaped(setup=[{"name": "browser", "run": "true"}, {"name": "tectonic", "run": "true", "lazy": True},
                          {"name": "plot", "run": "true", "lazy": True}])
        p = ps.plan("demo", self.folder, d)
        self.assertEqual((p["setup"], p["lazy"]), (["browser"], ["tectonic", "plot"]))
        self.assertEqual(ps.lines(p)[1:3], ["第 1 級，install.sh 自己跑的：browser", "第一次用到時才自動裝：tectonic、plot"],
                         "#display-dev: a lazy item is not one install.sh runs")
        p = ps.plan("demo", self.folder, shaped(setup=[{"name": "big", "run": "true", "lazy": True}]))
        self.assertEqual(ps.lines(p)[1:3], ["第 1 級，install.sh 自己跑的：沒有", "第一次用到時才自動裝：big"])
        d = shaped(guide=[{"id": "a", "title": "A", "check": "false", "run": "{plugin}/bin/go",
                           "secret": {"name": "rig-token", "label": "Rig token"}}])
        p = ps.plan("demo", self.folder, d)
        self.assertEqual(p["level"], 3)
        self.assertEqual(p["guide"][0]["run"], os.path.join(self.folder, "bin/go"))
        self.assertIn("1. ⬜ A（a）：還沒好（金鑰：這台還沒存；在自己的終端機跑 altention secret set demo rig-token）　← 下一步",
                      "\n".join(ps.lines(p)))


class OsName(unittest.TestCase):
    def test_os_release_like_words(self):
        for text, want in (('ID=ubuntu\nID_LIKE=debian\n', "debian"), ('ID="rocky"\nID_LIKE="rhel centos fedora"\n', "fedora"),
                           ('ID=manjaro\nID_LIKE=arch\n', "arch"), ('ID=alpine\n', "")):
            with mock.patch.object(ps.platform, "system", return_value="Linux"), \
                    mock.patch("builtins.open", mock.mock_open(read_data=text)):
                self.assertEqual(ps.os_name(), want, text)
        with mock.patch.object(ps.platform, "system", return_value="Darwin"):
            self.assertEqual(ps.os_name(), "macos")


class Fields(unittest.TestCase):
    def bad(self, words, **more):
        with self.assertRaises(plugin.PluginError) as e:
            shaped(**more)
        self.assertIn(words, str(e.exception))

    def test_guide_shapes(self):
        shaped(guide=[{"id": "a", "title": "A", "check": None, "ask": "?"},
                      {"id": "b", "title": "B", "check": "true", "do": ["x"], "after": ["a"], "gate": ["tools"],
                       "run": "true", "optional": True, "url": "https://example.org", "why": "w",
                       "secret": {"name": "rig-token", "label": "L"}}],
               offer={"cost": "15 分鐘", "when": "install"})
        self.bad("guide[0] 的 check 是 null，要寫 ask", guide=[{"id": "a", "title": "A", "check": None}])
        self.bad("guide[0] 的 check 是 null，要寫 ask", guide=[{"id": "a", "title": "A"}])
        self.bad("不重複", guide=[{"id": "a", "title": "A", "check": "x"}, {"id": "a", "title": "B", "check": "x"}])
        self.bad("guide[0].after 只能寫它前面的步驟的 id", guide=[{"id": "a", "title": "A", "check": "x", "after": ["b"]},
                                                         {"id": "b", "title": "B", "check": "x"}])
        self.bad("要是 secrets[] 裡的名字", guide=[{"id": "a", "title": "A", "check": "x",
                                                "secret": {"name": "other", "label": "L"}}])
        self.bad("guide[0].do 要是文字的清單", guide=[{"id": "a", "title": "A", "check": "x", "do": [1]}])
        self.bad("guide[0].check 要是指令", guide=[{"id": "a", "title": "A", "check": 3, "ask": "?"}])
        self.bad("offer 要是物件", offer={"cost": 1})

    def test_needs_fields(self):
        shaped(needs=[{"name": "x", "check": "true", "why": "w", "size": "10MB", "optional": True, "for": ["pdf"],
                       "ask": "?", "install": {"debian": "sudo apt install x", "macos": "brew install x"}}])
        self.bad("needs[0].install 要寫成", needs=[{"name": "x", "check": "true", "install": {"debian": ["apt"]}}])
        self.bad("needs[0].for 要是文字的清單", needs=[{"name": "x", "check": "true", "for": [1]}])
        self.bad("needs[0].optional 型別不對", needs=[{"name": "x", "check": "true", "optional": "yes"}])

    def test_a_guide_with_secrets_not_a_list_is_refused_not_a_crash(self):
        self.bad("secrets 要是清單", secrets=3, guide=[{"id": "a", "title": "A", "check": "x"}])


if __name__ == "__main__":
    unittest.main()
