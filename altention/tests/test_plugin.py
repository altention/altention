"""altention.plugin: reading and checking plugin.json, finding plugins, where each runs, filling in {tokens}."""
import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))))
from altention import testing as _testing  # noqa: E402
_testing.guard_home()  # tests never touch the real home (altention/testing.py)
import io
import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

from altention import config, plugin


def good(**more):
    d = {"format": 1, "name": "project-a", "title": "A", "description": "does a", "machines": "all"}
    d.update(more)
    return d


def write(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(data if isinstance(data, str) else json.dumps(data, ensure_ascii=False))


class Check(unittest.TestCase):
    def bad(self, d, words):
        with self.assertRaises(plugin.PluginError) as e:
            plugin.check(d, "x/plugin.json")
        self.assertIn("x/plugin.json", str(e.exception))
        self.assertIn(words, str(e.exception))

    def test_a_good_one_passes_and_unknown_fields_are_ignored(self):
        d = good(hooks=[{"event": "Stop", "command": "{python} x", "timeout": 10, "matcher": "Agent"}],
                 schedules=[{"name": "s", "when": {"every": "10m"}, "do": {"run": "x"}}],
                 setup=[{"name": "a", "run": None, "today": "install.sh"}],
                 data=[{"name": "d", "path": "{data}/d", "sync": "none", "private": True}], future_field={"x": 1})
        self.assertIs(plugin.check(d), d)

    def test_what_is_wrong_is_named(self):
        self.bad(good(name=""), "name")
        self.bad(good(name="Project A"), "小寫")
        self.bad(good(machines="two"), "machines")
        self.bad(good(hooks=[{"event": "Stop"}]), "hooks[0] 少了 command")
        self.bad(good(schedules=[{"name": "s", "when": {"at": "08:00", "every": "1h"}, "do": {"run": "x"}}]), "剛好一種")
        self.bad(good(schedules=[{"name": "s", "when": {"at": "08:00"}, "do": {}}]), "message、run")
        self.bad(good(setup=[{"name": "a"}]), "setup[0].run")
        self.bad(good(data=[{"name": "d", "path": "p", "sync": "hourly"}]), "data[0].sync")
        self.bad(good(cron=[{"name": "c", "when": "@reboot", "run": "x"}]), "只有核心")
        self.bad(good(schedules=[{"name": "s", "when": {"at": "8:5"}, "do": {"run": "x"}}]), "HH:MM")
        self.bad(good(schedules=[{"name": "s", "when": {"every": "10m", "offset": "15m"}, "do": {"run": "x"}}]), "比 every 小")
        self.bad(good(name="core"), "不能叫 core")  # a file elsewhere calling itself core gets nothing of the core's
        self.bad([], "不是 JSON 物件")
        self.bad(good(pitch=["a"]), "pitch 要是文字")
        self.bad(good(keep=1), "keep 要是文字")
        self.bad(good(lose=None), "lose 要是文字")
        self.bad(good(order="10"), "order 要是整數")
        self.bad(good(order=True), "order 要是整數")
        self.assertTrue(plugin.check(good(pitch="給使用者看的一句", order=10)))

    def test_the_sync_and_the_engine_update_never_go_into_the_scheduler(self):
        """DESIGN 8: they must keep running when the time zone, plugins.json or the scheduler's state are broken."""
        for run in ("python3 -m altention.sync", "python3 altention/update.py",
                    "~/agent-home/bin/home-pull --quiet", "cd x && git pull -q"):
            item = {"name": "s", "when": {"every": "15m"}, "do": {"run": run}}
            self.bad(good(schedules=[item]), "永遠不進排程器")
            plugin.check(good(schedules=[dict(item, scheduler=False)]))  # kept in its own cron line: allowed
        plugin.check(good(schedules=[{"name": "e", "when": {"every": "15m"}, "do": {"run": "chatlog.py export"}}]))
        for run in ('git -C "$H" pull -q --ff-only', "git -C x fetch -q"):
            self.bad(good(schedules=[{"name": "g", "when": {"every": "1h"}, "do": {"run": run}}]), "永遠不進排程器")
        # not a sync; chatlog.py sync only exports since 3a.10 (the core's cron_sync.py syncs), so it may go in
        for run in ("chatlog.py sync-status", "tools/x --git pull-requests", '{python} "{plugin}/chatlog.py" sync'):
            plugin.check(good(schedules=[{"name": "n", "when": {"every": "1h"}, "do": {"run": run}}]))
        self.bad(good(schedules=[{"name": "z", "when": {"every": "1h"}, "do": {"run": "x"}, "scheduler": 0}]), "true 或 false")
        self.bad(good(schedules=[{"name": "s", "when": {"every": "1h"}, "do": {"run": "x"}, "scheduler": "no"}]), "true 或 false")

    def test_a_newer_format_is_read_with_a_warning(self):
        err = io.StringIO()
        with mock.patch("sys.stderr", err):
            plugin.check(good(format=2))
        self.assertIn("比這版引擎認得的 1 新", err.getvalue())

    def test_the_core_may_have_cron(self):
        plugin.check(good(name="core", cron=[{"name": "c", "when": "@reboot", "run": "x"}]), core=True)

    def test_a_plugin_elsewhere_named_core_is_skipped_when_the_real_one_is_missing(self):
        tmp = tempfile.mkdtemp()
        try:
            write(os.path.join(tmp, "engine", "chatlog", "plugin.json"), good(name="chatlog"))
            write(os.path.join(tmp, "root", "plugins", "evil", "plugin.json"),
                  good(name="core", cron=[{"name": "c", "when": "@reboot", "run": "x"}]))
            plugins, problems = plugin.discover(os.path.join(tmp, "engine"), "", os.path.join(tmp, "root"))
            self.assertEqual([n for n, _, _ in plugins], ["chatlog"])
            self.assertTrue(any("不能叫 core" in p for p in problems))
        finally:
            shutil.rmtree(tmp)


class Finding(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="外掛 plugin ")
        self.engine = os.path.join(self.tmp, "engine")
        self.root = os.path.join(self.tmp, "altention")
        self.inst = os.path.join(self.root, "instance")
        write(os.path.join(self.engine, "altention", "core.plugin.json"), good(name="core"))
        write(os.path.join(self.engine, "chatlog", "plugin.json"), good(name="chatlog"))
        write(os.path.join(self.engine, "skills", "council", "plugin.json"), good(name="council"))
        write(os.path.join(self.root, "plugins", "life 生活", "plugin.json"), good(name="life"))
        write(os.path.join(self.inst, "plugins", "mine", "plugin.json"), good(name="mine", machines="one"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def found(self):
        return plugin.discover(self.engine, self.inst, self.root)

    def test_all_places_the_core_first(self):
        plugins, problems = self.found()
        self.assertEqual([n for n, _, _ in plugins], ["core", "chatlog", "council", "life", "mine"])
        self.assertEqual(problems, [])
        self.assertTrue(plugins[3][1].endswith("life 生活"))

    def test_a_bad_file_is_a_problem_and_the_rest_still_load(self):
        write(os.path.join(self.engine, "usage", "plugin.json"), "{not json")
        write(os.path.join(self.engine, "progress", "plugin.json"), good(name="chatlog"))  # a second "chatlog"
        plugins, problems = self.found()
        self.assertIn("chatlog", [n for n, _, _ in plugins])
        self.assertEqual(len(problems), 2)
        self.assertTrue(any("不是合法的 JSON" in p for p in problems))
        self.assertTrue(any("重複" in p for p in problems))

    def test_a_folder_linked_in_twice_counts_once(self):
        os.symlink(os.path.join(self.root, "plugins", "life 生活"), os.path.join(self.engine, "plugins-link"))
        os.makedirs(os.path.join(self.engine, "plugins"))
        os.symlink(os.path.join(self.root, "plugins", "life 生活"), os.path.join(self.engine, "plugins", "life"))
        names = [n for n, _, _ in self.found()[0]]
        self.assertEqual(names.count("life"), 1)


class TheEnginesOwn(unittest.TestCase):
    def test_every_plugin_json_in_this_engine_loads(self):
        plugins, problems = plugin.discover(plugin.ENGINE, instance="", root=tempfile.gettempdir())
        self.assertEqual(problems, [])
        self.assertEqual([n for n, _, _ in plugins][0], "core")


    def test_every_bundled_plugin_says_what_it_does_for_the_user_in_the_owners_order(self):
        """The install asks about each one by its pitch, in this order (owner 2026-10-15); no plan or stage words."""
        plugins, _ = plugin.discover(plugin.ENGINE, instance="", root=tempfile.gettempdir())
        rest = [(d.get("order"), n, d) for n, _, d in plugins if n != "core"]
        self.assertEqual([n for _, n, _ in sorted(rest, key=lambda r: r[0])],
                         ["chatlog", "progress", "planner", "council", "usage", "status"])
        for _, n, d in rest:
            self.assertTrue(d.get("pitch", "").strip(), n)
            self.assertTrue(d.get("keep", "").strip() and d.get("lose", "").strip(), n)
            for words in ("階段", "stage", "併進"):
                self.assertNotIn(words, d["description"] + d["pitch"], n)


class Placement(unittest.TestCase):
    SETTINGS = {"plugins": {"usage": {"on": "host-a"}, "progress": {"on": ["host-b"]}, "chatlog": {"disabled_on": ["host-c"]}}}

    def where(self, name, machines, machine, bot="host-a", settings=SETTINGS, known=("host-a", "host-b", "host-c")):
        return plugin.placement(name, good(name=name, machines=machines), machine, settings, bot, known)

    def test_all_runs_everywhere_unless_disabled_on_purpose(self):
        self.assertEqual(self.where("chatlog", "all", "host-a"), "on")
        self.assertEqual(self.where("chatlog", "all", "host-c"), "disabled")

    def test_one_runs_where_the_instance_says_else_on_the_bots_machine(self):
        self.assertEqual(self.where("usage", "one", "host-a"), "on")
        self.assertEqual(self.where("usage", "one", "host-b"), "off")
        self.assertEqual(self.where("progress", "one", "host-b"), "on")
        self.assertEqual(self.where("planner", "one", "host-a"), "on", "not in the instance: the bot's machine")
        self.assertEqual(self.where("planner", "one", "host-b"), "off")

    def test_what_cannot_be_read_is_unknown_never_off(self):
        """A broken plugins.json or machines.json (pushed to every machine) must not take cron lines away."""
        self.assertEqual(self.where("progress", "one", "host-b", settings=None), "unknown")
        self.assertEqual(self.where("chatlog", "all", "host-a", settings=None), "unknown")
        self.assertEqual(self.where("planner", "one", "host-a", bot=None), "unknown", "no machine list, no default")
        self.assertEqual(self.where("usage", "one", "host-a", known=("host-x",)), "unknown", "on names no known machine")
        self.assertEqual(self.where("chatlog", "all", "host-a", bot=None), "on", "all needs no machine list")

    def test_a_machine_not_on_the_list_that_was_read_is_unknown(self):
        """A machine that cannot work out its own name (hostname changed, host file wrong) is not "off": its lines
        would be taken for another machine's and removed (code-review 2026-10-06)."""
        for name, machines in (("usage", "one"), ("progress", "one"), ("planner", "one"), ("chatlog", "all")):
            self.assertEqual(self.where(name, machines, "host-a-lab"), "unknown", name)
        self.assertIn("不在機器清單上", plugin.why_unknown("progress", self.SETTINGS, "host-a", {"host-a"}, "host-a-lab"))
        self.assertEqual(self.where("chatlog", "all", "box", known=None), "on", "no list at all: one machine, as before")

    def test_the_core_always_runs(self):
        self.assertEqual(self.where("core", "all", "host-a", settings={"plugins": {"core": {"disabled_on": ["host-a"]}}}), "on")
        self.assertEqual(self.where("core", "all", "host-a", settings=None), "on")

    def test_steps_with_on(self):
        here = plugin.step_here
        self.assertTrue(here({}, "host-b", False, "host-a"))
        self.assertTrue(here({"on": "bot"}, "host-a", False, "host-a"))
        self.assertFalse(here({"on": "bot"}, "host-b", True, "host-a"))
        self.assertTrue(here({"on": ["one", "sources"]}, "host-a", True, "host-a", ["host-b"]), "where it runs")
        self.assertTrue(here({"on": ["one", "sources"]}, "host-b", False, "host-a", ["host-b"]), "a source")
        self.assertFalse(here({"on": ["one", "sources"]}, "host-z", False, "host-a", ["host-b"]), "neither")
        self.assertFalse(here({"on": ["sources"]}, "host-c", False, "host-a", ["host-b"]))

    def test_unknown_words_in_on_are_reported(self):
        self.assertEqual(plugin.unknown_on({"on": ["one", "sourcs", "host-b"]}, ["host-b"]), ["sourcs"])


class RealConfig(unittest.TestCase):
    """on_this_machine() through the real altention.config: a throwaway home and instance with machines.json and
    plugins.json, and this engine's own plugin.json files (no config function stood in for)."""
    MACHINES = {"format": 1, "machines": {"host-a": {"ssh": ["local"], "roles": ["chatlog", "dcbot", "progress"]},
                                          "host-b": {"ssh": ["host-b"], "roles": ["chatlog", "dcattach"]}}}

    def setUp(self):
        self.inst = tempfile.mkdtemp(prefix="實例 instance ")
        write(os.path.join(self.inst, "machines.json"), self.MACHINES)
        write(os.path.join(self.inst, "plugins.json"), {"format": 1})
        self.env = mock.patch.dict(os.environ, {"ALT_INSTANCE": self.inst})
        self.env.start()
        host = os.path.expanduser("~/.config/altention/host")
        os.makedirs(os.path.dirname(host), exist_ok=True)
        write(host, "host-a\n")

    def tearDown(self):
        self.env.stop()
        shutil.rmtree(self.inst, ignore_errors=True)
        os.remove(os.path.expanduser("~/.config/altention/host"))

    def placements(self, machine):
        rows, problems = plugin.on_this_machine(machine, root=tempfile.gettempdir())
        return {n: (w, r) for n, _, _, w, r in rows}, problems

    def test_one_plugins_run_on_the_bots_machine_only(self):
        here, problems = self.placements("host-a")
        self.assertEqual(problems, [])
        self.assertEqual({n: w for n, (w, _) in here.items()},
                         {"core": "on", "chatlog": "on", "council": "on", "progress": "on", "planner": "on", "status": "on",
                          "usage": "on"})
        there, _ = self.placements("host-b")
        self.assertEqual([n for n, (w, _) in there.items() if w == "off"], ["planner", "progress", "status", "usage"])

    def test_a_broken_plugins_json_makes_them_unknown_with_the_reason(self):
        write(os.path.join(self.inst, "plugins.json"), "{broken")
        here, problems = self.placements("host-a")
        self.assertEqual(here["progress"][0], "unknown")
        self.assertEqual(here["chatlog"][0], "unknown")
        self.assertEqual(here["core"][0], "on")
        self.assertIn("讀不出來", here["progress"][1])
        self.assertTrue(problems)

    def test_a_broken_machine_list_without_a_machine_name_gives_unknowns_not_an_error(self):
        """The self-repair and the sync call without a name: they must get the unknowns and the reasons."""
        write(os.path.join(self.inst, "machines.json"), "{broken")
        rows, problems = plugin.on_this_machine(root=tempfile.gettempdir())
        here = {n: (w, r) for n, _, _, w, r in rows}
        self.assertEqual(here["progress"][0], "unknown")
        self.assertIn("不知道機器人在哪台", here["progress"][1])
        self.assertEqual(here["chatlog"][0], "on", "all needs no machine list")
        self.assertTrue(any("機器清單" in p for p in problems))

    def test_no_machine_with_the_bot_role_is_said(self):
        m = json.loads(json.dumps(self.MACHINES))
        m["machines"]["host-a"]["roles"] = ["chatlog"]
        write(os.path.join(self.inst, "machines.json"), m)
        here, _ = self.placements("host-a")
        self.assertEqual(here["progress"][0], "unknown")
        self.assertIn("dcbot", here["progress"][1])

    def test_on_of_the_wrong_kind_is_unknown_with_the_reason(self):
        write(os.path.join(self.inst, "plugins.json"), {"format": 1, "plugins": {"progress": {"on": 5}, "usage": "x"}})
        here, _ = self.placements("host-a")
        self.assertEqual(here["progress"], ("unknown", "實例設定裡 plugins.progress.on 寫的不是機器名或機器名清單"))
        self.assertEqual(here["usage"][0], "unknown")

    def test_no_instance_here_is_unknown(self):
        with mock.patch.dict(os.environ, {"ALT_INSTANCE": os.path.join(self.inst, "gone")}), \
                mock.patch.object(config, "root", return_value=os.path.join(self.inst, "nowhere")):
            here, problems = self.placements("host-a")
        self.assertEqual(here["chatlog"][0], "unknown")
        self.assertTrue(any("沒有實例" in p for p in problems))

    def test_a_plugin_json_that_cannot_be_read_is_listed_unknown(self):
        engine = tempfile.mkdtemp()
        try:
            write(os.path.join(engine, "altention", "core.plugin.json"), good(name="core"))
            write(os.path.join(engine, "progress", "plugin.json"), "{broken")
            rows, problems = plugin.on_this_machine("host-a", engine=engine, root=tempfile.gettempdir())
            self.assertIn(("progress", "unknown", "plugin.json 讀不出來"), [(n, w, r) for n, _, _, w, r in rows])
        finally:
            shutil.rmtree(engine)


class Expand(unittest.TestCase):
    def test_tokens(self):
        s = {"usage": {"channel": "100000000000000001", "plan": ["Max", 20]}}
        self.assertEqual(plugin.expand("{setting:usage.channel}", settings=s), "100000000000000001")
        self.assertEqual(plugin.expand("{setting:usage.plan}", settings=s), '["Max", 20]')
        self.assertEqual(plugin.expand("remind.py {setting:a.b}", settings={"a": {"b": []}}), "remind.py ", "no files: none")
        files = {"planner": {"schedule_files": ["~/english/schedule.json", "/data/有 空白.json"]}}
        with mock.patch.dict(os.environ, {"HOME": "/home/alice"}):
            self.assertEqual(plugin.expand("remind.py {setting:planner.schedule_files}", settings=files),
                             "remind.py /home/alice/english/schedule.json '/data/有 空白.json'")
        self.assertEqual(plugin.expand('"{plugin}/x.py"', folder="/p 中 文"), '"/p 中 文/x.py"')
        self.assertTrue(plugin.expand("{engine}/dcbot").endswith("/dcbot"))
        self.assertIn("python3", plugin.expand("{python}"))
        if os.path.exists("/usr/bin/python3"):
            with mock.patch.dict(os.environ, {"PATH": "/some/venv/bin:" + os.environ.get("PATH", "")}):
                self.assertEqual(plugin.expand("{python}"), "/usr/bin/python3", "not a venv's")

    def test_missing_ones_are_errors(self):
        with self.assertRaises(plugin.PluginError):
            plugin.expand("{setting:usage.nope}", settings={})
        with self.assertRaises(plugin.PluginError):
            plugin.expand("{plugin}/x")

    def test_home_is_read_when_expanding(self):
        with mock.patch.dict(os.environ, {"HOME": "/home/alice", "ALT_ROOT": "/home/alice/altention"}):
            self.assertEqual(plugin.expand("~/.cache/progress/cron.out"), "/home/alice/.cache/progress/cron.out")
            self.assertEqual(plugin.expand("{root}/engine"), "/home/alice/altention/engine")
            with mock.patch.dict(os.environ, {"ALT_DATA": ""}):
                self.assertEqual(plugin.expand("{data}/logs"), "/home/alice/altention/data/logs")


if __name__ == "__main__":
    unittest.main()
