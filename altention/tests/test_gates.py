"""C-77 G5: a guide[] step's gate holds its setup items and schedules back until the step is done (only a check that
said not done holds; once let go, never held again), and a new version asking more of the person counts as perms.
In a throwaway home."""
import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))))
from altention import testing as _testing  # noqa: E402
_testing.guard_home()  # tests never touch the real home (altention/testing.py)
import os
import shutil
import tempfile
import unittest
from unittest import mock

from altention import plugin_steps as ps, plugin_update as pu, scheduler

_sys.path.insert(0, _os.path.join(ps.plugin.ENGINE, "ops"))
import plancore  # noqa: E402


def demo(folder, check='test -e "{plugin}/done"', gate=("tools", "remind")):
    return {"name": "demo", "setup": [{"name": "tools", "run": 'echo ran >> "{plugin}/ran"'},
                                      {"name": "other", "run": 'echo other >> "{plugin}/ran"'}],
            "schedules": [{"name": "remind", "when": {"every": "5m"}, "do": {"run": "true"}},
                          {"name": "free", "when": {"every": "5m"}, "do": {"run": "true"}}],
            "guide": [{"id": "key", "title": "拿金鑰", "check": check, "gate": list(gate)},
                      {"id": "ask", "title": "問", "check": None, "ask": "?", "gate": ["free"]}]}


class Rig(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="家 g5 ")
        self.addCleanup(shutil.rmtree, self.home, True)
        env = mock.patch.dict(os.environ, {"HOME": self.home})
        env.start()
        self.addCleanup(env.stop)
        self.folder = os.path.join(self.home, "插件 demo")
        os.makedirs(self.folder)
        self.d = demo(self.folder)

    def done(self):
        open(os.path.join(self.folder, "done"), "w").close()

    def ran(self):
        try:
            with open(os.path.join(self.folder, "ran")) as f:
                return f.read().split()
        except OSError:
            return []


class Gates(Rig):
    def test_held_while_failing_let_go_for_good(self):
        self.assertEqual(ps.gated("demo", self.d), {}, "no plan yet: nothing held")
        ps.plan("demo", self.folder, self.d)
        self.assertEqual(ps.gated("demo", self.d), {"tools": "拿金鑰", "remind": "拿金鑰"},
                         "a failing check holds; a step nothing can check holds nothing")
        self.done()
        ps.plan("demo", self.folder, self.d)
        self.assertEqual(ps.gated("demo", self.d), {})
        os.remove(os.path.join(self.folder, "done"))
        ps.plan("demo", self.folder, self.d)
        self.assertEqual(ps.gated("demo", self.d), {}, "failing again later takes nothing away")

    def test_a_step_not_checked_this_time_keeps_what_it_was(self):
        ps.plan("demo", self.folder, self.d)
        ps.plan("demo", self.folder, self.d, deadline=0)
        self.assertEqual(sorted(ps.gated("demo", self.d)), ["remind", "tools"])
        self.done()
        ps.plan("demo", self.folder, self.d, deadline=0)
        self.assertEqual(sorted(ps.gated("demo", self.d)), ["remind", "tools"], "unchecked lets nothing go")

    def test_an_unreadable_file_holds_nothing(self):
        ps.plan("demo", self.folder, self.d)
        with open(ps.gate_path("demo"), "w") as f:
            f.write("{")
        self.assertEqual(ps.gated("demo", self.d), {})
        self.assertTrue(ps.gate_path("demo").startswith(self.home))

    def test_setup_items_wait_and_run_once_let_go(self):
        ps.plan("demo", self.folder, self.d)
        ran = pu.run_setup("demo", self.folder, self.d, "v1")
        self.assertEqual([(n, s) for n, s, _ in ran], [("tools", "gated"), ("other", "ran")])
        self.assertEqual(ran[0][2], "等「拿金鑰」做完")
        self.assertEqual(self.ran(), ["other"])
        self.assertEqual([(n, s) for n, s, _ in pu.run_setup("demo", self.folder, self.d, "v1", only="tools")], [("tools", "ran")],
                         "run by hand: the person asked for it")
        os.remove(os.path.join(self.folder, "ran"))
        self.done()
        ps.plan("demo", self.folder, self.d)
        self.assertEqual([(n, s) for n, s, _ in pu.run_setup("demo", self.folder, self.d, "v2")], [("tools", "ran"), ("other", "ran")])

    def test_a_machine_following_a_new_version_makes_its_gates_first_and_says_them(self):
        """support-2 of 0e39da8: switch() (the update round, update plugin) makes the plan before the new version's
        setup, so a machine that never made one holds the gated item too, and the result says what waits."""
        with mock.patch.object(pu, "ensure", return_value=("moved", "換到 v2", "v2")), \
                mock.patch.object(pu, "folder_of", return_value=self.folder), \
                mock.patch.object(pu.plugin, "link_skills"):
            import json as _json
            with open(os.path.join(self.folder, "plugin.json"), "w") as f:
                _json.dump(dict(self.d, format=1, title="D", description="d"), f)
            self.assertEqual(ps.gated("demo", self.d), {}, "no plan on this machine yet")
            ok, why = pu.switch({"name": "demo", "source": "x", "version": "v1"}, "v2", settings={})
        self.assertTrue(ok, why)
        self.assertEqual(why, "換到 v2；tools 等「拿金鑰」做完")
        self.assertEqual(self.ran(), ["other"])

    def test_a_plan_that_fails_in_switch_is_no_gate_and_is_logged(self):
        logged = []
        with mock.patch.object(pu, "ensure", return_value=("moved", "換到 v2", "v2")), \
                mock.patch.object(pu, "folder_of", return_value=self.folder), \
                mock.patch.object(pu.plugin, "link_skills"), \
                mock.patch.object(ps, "plan", side_effect=ValueError("broken")):
            import json as _json
            with open(os.path.join(self.folder, "plugin.json"), "w") as f:
                _json.dump(dict(self.d, format=1, title="D", description="d"), f)
            ok, why = pu.switch({"name": "demo", "source": "x", "version": "v1"}, "v2", settings={}, log=logged.append)
        self.assertEqual((ok, why), (True, "換到 v2"))
        self.assertEqual(sorted(self.ran()), ["other", "ran"])
        self.assertTrue(any("算不出計畫（ValueError: broken）" in x for x in logged), logged)

    def test_the_scheduler_leaves_a_gated_item_out_unless_it_ran_here(self):
        ps.plan("demo", self.folder, self.d)
        path = os.path.join(self.folder, "plugin.json")
        names = lambda **k: [i["name"] for i in scheduler.overridden("demo", self.d, path, "box", **k)]  # noqa: E731
        with mock.patch.object(scheduler.config, "settings", return_value={}):
            with mock.patch.object(scheduler, "read_state", return_value={}):
                self.assertEqual(names(), ["free"], "never seen on this machine: held")
            with mock.patch.object(scheduler, "read_state", return_value={scheduler.sent_key(path, "remind"): {"slot": 1}}):
                self.assertEqual(names(), ["remind", "free"], "it ran here already: kept, as cron keeps its line (#code-review of 0e39da8)")
            with mock.patch.object(scheduler, "read_state", side_effect=scheduler.StateUnreadable("x")):
                self.assertEqual(names(), ["remind", "free"], "no state to read: nothing held")
            self.assertEqual([i["name"] for i in scheduler.overridden("demo", self.d)], ["remind", "free"], "no machine: nothing held")
            self.done()
            ps.plan("demo", self.folder, self.d)
            with mock.patch.object(scheduler, "read_state", return_value={}):
                self.assertEqual(names(), ["remind", "free"])

    def test_plan_adds_no_line_for_a_gated_item_and_keeps_one_there(self):
        ps.plan("demo", self.folder, self.d)
        rows = [("demo", self.folder, self.d, "on", "")]
        want, unknown = plancore.wanted(rows, "box", {}, "box")
        self.assertEqual(unknown, [])
        self.assertEqual([(w["name"], w["kind"]) for w in want if w["plugin"] == "demo"], [("remind", "gated"), ("free", "cron")])
        result = plancore.compare(want, [], [])
        self.assertIn("gated", [r[0] for r in result if r[1] and r[1]["name"] == "remind"])
        self.assertNotIn("missing", [r[0] for r in result if r[1] and r[1]["name"] == "remind"])
        self.assertEqual(next(r[1]["step"] for r in result if r[0] == "gated"), "拿金鑰")


class AsksMore(unittest.TestCase):
    def test_tools_steps_runs_and_keys_count_as_perms(self):
        old = {"needs": [{"name": "a", "check": "x", "install": {"debian": "apt a"}}],
               "guide": [{"id": "s", "title": "S", "check": "x", "run": "go"}], "secrets": [{"name": "k"}]}
        new = {"needs": [{"name": "a", "check": "x", "install": {"debian": "apt a", "macos": "brew a"}},
                         {"name": "b", "check": "x", "install": {"debian": "apt b"}, "size": "約 5MB"},
                         {"name": "c", "check": "x"}],
               "guide": [{"id": "s", "title": "S", "check": "x", "run": "go2"}, {"id": "t", "title": "T", "check": "x"}],
               "secrets": [{"name": "k"}, {"name": "j", "label": "J 金鑰"}]}
        self.assertEqual(pu.asks_more(old, new), ["要你裝新的工具 a", "要你裝新的工具 b（約 5MB）", "改了插件代做的步驟「S」：go2",
                                                  "要你到別處多做一步：T", "要你多交一把金鑰：J 金鑰"],
                         "install compared whole, every system's")
        self.assertEqual(pu.asks_more(new, new), [])
        self.assertEqual(pu.added(old, new)[-1], "要你多交一把金鑰：J 金鑰")
        self.assertEqual(pu.watchers(old, new), [], "none of these sees the conversations")


if __name__ == "__main__":
    unittest.main()
