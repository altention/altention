"""The plugin.json files of what exists today: they parse, name what is really there, and the core's hooks
are exactly the ones dchook installs."""
import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))))
from altention import testing as _testing  # noqa: E402
_testing.guard_home()  # tests never touch the real home (altention/testing.py)
import json
import os
import re
import sys
import unittest


def engine():
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def files():
    return {"core": "altention/core.plugin.json", "chatlog": "chatlog/plugin.json", "progress": "progress/plugin.json",
            "usage": "usage/plugin.json", "planner": "planner/plugin.json", "council": "skills/council/plugin.json",
            "status": "status/plugin.json"}


def load(name):
    with open(os.path.join(engine(), files()[name]), encoding="utf-8") as f:
        return json.load(f)


def folder(name):
    return os.path.dirname(os.path.join(engine(), files()[name]))


class PluginJson(unittest.TestCase):
    def test_each_parses_and_names_itself(self):
        for name in files():
            d = load(name)
            self.assertEqual(d["name"], name)
            self.assertEqual(d["format"], 1)
            self.assertIn(d["machines"], ("all", "one"), name)
            for key in ("title", "description"):
                self.assertTrue(d.get(key), "%s: %s" % (name, key))

    def test_core_hooks_are_the_ones_dchook_installs(self):
        from altention.connectors.claude_code import hooks as hookspec
        command = "'{engine}/dcbot/tools/dchook' --global"
        want = sorted((ev, g.get("matcher") or "", h["command"], h.get("timeout"))
                      for ev, groups in hookspec.hooks(command, 20).items() for g in groups for h in g["hooks"])
        have = sorted((h["event"], h.get("matcher") or "", h["command"], h.get("timeout")) for h in load("core")["hooks"])
        self.assertEqual(have, want)
        self.assertEqual(len(have), 13)  # (12, and the secrets guard of K-03)

    def test_commands_point_at_files_that_exist(self):
        for name in files():
            d = load(name)
            lines = [h["command"] for h in d.get("hooks", [])]
            lines += [s["do"]["run"] for s in d.get("schedules", []) if s["do"].get("run")]
            lines += [s["run"] for s in d.get("setup", []) if s.get("run")] + [c["run"] for c in d.get("cron", [])]
            for line in lines:
                for where, rel in re.findall(r"\{(plugin|engine)\}/([\w./-]+)", line):
                    base = folder(name) if where == "plugin" else engine()
                    self.assertTrue(os.path.exists(os.path.join(base, rel)), "%s: %s" % (name, line))
            for s in d.get("skills", []):
                self.assertTrue(os.path.isfile(os.path.join(folder(name), s["path"], "SKILL.md")), "%s: %s" % (name, s))

    def test_schedules_have_one_time_and_one_action(self):
        for name in files():
            for s in load(name).get("schedules", []):
                self.assertEqual(len(set(s["when"]) & {"at", "every", "once"}), 1, "%s: %s" % (name, s["name"]))
                self.assertEqual(len(set(s["do"]) & {"message", "run"}), 1, "%s: %s" % (name, s["name"]))

    def test_data_says_how_it_is_synced(self):
        for name in files():
            for item in load(name).get("data", []):
                self.assertIn(item["sync"], ("timer", "on-request", "none"), "%s: %s" % (name, item["name"]))

    def test_nothing_of_one_installation(self):
        for name in files():
            with open(os.path.join(engine(), files()[name]), encoding="utf-8") as f:
                text = f.read()
            self.assertIsNone(re.search(r"\d{17,20}|git@|https?://|/mnt/|/Users/", text), name)


if __name__ == "__main__":
    unittest.main()
