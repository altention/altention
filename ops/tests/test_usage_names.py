"""11-12: usage.project_names in the instance's plugins.json, {"folder name": "shown name"}: the projects board and the
chart show folders under that name, folders given one name are one row with their costs added; folders not in it are
shown as before; a value that is not such a map is not used, said in the cron log and noted for doctor. spend.json
keeps the folder names."""
import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))))
from altention import testing as _testing  # noqa: E402
_testing.guard_home()  # tests never touch the real home (altention/testing.py)
import importlib.util
import io
import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

ENGINE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
spec = importlib.util.spec_from_file_location("usage_collect_names", os.path.join(ENGINE, "usage", "collect.py"))
collect = importlib.util.module_from_spec(spec)
spec.loader.exec_module(collect)


def row(project, cost, agent="claude", tokens=100, last="2026-11-10T00:00:00Z"):
    return {"agent": agent, "project": project, "cost": cost, "tokens": tokens, "last": last}


class TheSetting(unittest.TestCase):
    def test_not_set_is_no_names(self):
        self.assertEqual(collect.project_names(None), ({}, None))

    def test_a_map_of_text(self):
        self.assertEqual(collect.project_names({"old-home": "proj", "engine": " proj "}),
                         ({"old-home": "proj", "engine": "proj"}, None))
        self.assertEqual(collect.project_names({}), ({}, None))

    def test_anything_else_is_not_used_and_said(self):
        for bad in (["old-home", "proj"], "proj", {"old-home": ""}, {"old-home": 3}, {"": "proj"}, {"a": "x", "b": None}):
            names, why = collect.project_names(bad)
            self.assertEqual(names, {}, bad)
            self.assertIn("usage.project_names", why)
            self.assertIn("看板先照資料夾名顯示", why)


class Merged(unittest.TestCase):
    def test_folders_given_one_name_are_one_row_added_up(self):
        results = [{"machine": "host-a", "projects": [row("engine", 30.0, tokens=300, last="2026-11-11"), row("other", 20.0),
                                                      row("old-home", 15.5, tokens=50, last="2026-11-09")]},
                   {"machine": "host-b", "projects": [row("engine", 1.0)]}]
        before = json.dumps(results)
        got = collect.merged(results, {"engine": "proj", "old-home": "proj"})
        self.assertEqual(json.dumps(results), before, "the input is left as it was (spend.json is written from it)")
        a = got[0]["projects"]
        self.assertEqual([(p["project"], p["cost"]) for p in a], [("proj", 45.5), ("other", 20.0)])
        self.assertEqual((a[0]["tokens"], a[0]["last"]), (350, "2026-11-11"))
        self.assertEqual([(p["project"], p["cost"]) for p in got[1]["projects"]], [("proj", 1.0)], "each machine its own rows")

    def test_agents_stay_apart(self):
        got = collect.merged([{"machine": "host-a", "projects": [row("engine", 3.0), row("old-home", 2.0, agent="codex")]}],
                             {"engine": "proj", "old-home": "proj"})
        self.assertEqual(sorted((p["project"], p["agent"]) for p in got[0]["projects"]), [("proj", "claude"), ("proj", "codex")])

    def test_no_names_and_machines_without_projects_as_they_are(self):
        results = [{"machine": "host-a", "error": "連不上"}, {"machine": "host-b", "projects": [row("x", 1.0)]}]
        self.assertIs(collect.merged(results, {}), results)
        self.assertEqual(collect.merged(results, {"y": "z"})[0], {"machine": "host-a", "error": "連不上"})

    def test_the_board_shows_one_row(self):
        got = collect.merged([{"machine": "host-a", "projects": [row("engine", 30.0), row("old-home", 15.5), row("other", 20.0)]}],
                             {"engine": "proj", "old-home": "proj"})
        board = collect.projects_board(got)
        self.assertIn("- proj（host-a）$46", board)
        self.assertNotIn("engine", board)
        self.assertNotIn("old-home", board)


class ARound(unittest.TestCase):
    """main() with the machine read stood in for: what the board, the chart, spend.json and state.json get."""

    def round(self, names=None, given=True):
        settings = {"usage": {"channel": "100000000000000001", "subscription_limits": False},
                    "plugins": {"usage": {"sources": ["host-a"]}}}
        if given:
            settings["usage"]["project_names"] = names
        tmp = tempfile.mkdtemp(prefix="usage names ")
        self.addCleanup(shutil.rmtree, tmp, True)
        posted, charted = {}, []

        def setting(path, default=None):
            node = settings
            for part in path.split("."):
                if not isinstance(node, dict) or part not in node:
                    return default
                node = node[part]
            return node
        from altention import config
        projects = [row("engine", 30.0), row("old-home", 15.5), row("other", 20.0)]
        with mock.patch.object(config, "setting", setting), \
                mock.patch.object(collect, "inventory", lambda: {"machines": {"host-a": {"ssh": ["local"]}}}), \
                mock.patch.object(collect, "machine_usage", lambda n, m: {"machine": n, "daily": [], "projects": list(projects),
                                                                          "sessions": [], "since": None}), \
                mock.patch.object(collect, "post", lambda board, text, file=None: posted.__setitem__(board, text) or 0), \
                mock.patch.object(collect, "chart", lambda today, results, *a: charted.append(results)), \
                mock.patch.object(collect, "SAMPLES", os.path.join(tmp, "samples.jsonl")), \
                mock.patch.object(collect, "spend_path", lambda: os.path.join(tmp, "spend.json")), \
                mock.patch.object(collect, "state_path", lambda: os.path.join(tmp, "state.json")), \
                mock.patch.object(collect.time, "sleep", lambda s: None), \
                mock.patch("sys.stderr", new_callable=io.StringIO) as err:
            collect.main()
        with open(os.path.join(tmp, "state.json")) as f:
            state = json.load(f)
        return posted, charted, err.getvalue(), state

    def test_named_folders_are_one_row_on_the_board_and_the_chart(self):
        posted, charted, err, state = self.round({"engine": "proj", "old-home": "proj"})
        self.assertIn("- proj（host-a）$46", posted["專案"])
        self.assertEqual([(p["project"], p["cost"]) for p in charted[0][0]["projects"]], [("proj", 45.5), ("other", 20.0)])
        self.assertNotIn("project_names", err)
        self.assertNotIn("names_problem", state)

    def test_not_set_shows_folders_as_before(self):
        posted, charted, err, state = self.round(given=False)
        self.assertIn("- engine（host-a）$30", posted["專案"])
        self.assertIn("- old-home（host-a）$16", posted["專案"])
        self.assertNotIn("names_problem", state)

    def test_miswritten_is_said_noted_and_folders_shown(self):
        posted, _, err, state = self.round(["engine", "proj"])
        self.assertIn("- engine（host-a）$30", posted["專案"])
        self.assertIn("usage.project_names", err)
        self.assertIn("usage.project_names", state["names_problem"])
        self.assertIn("last_run", state, "the round itself went through")

    def test_fixed_clears_the_note(self):
        _, _, _, state = self.round(["engine", "proj"])
        self.assertIn("names_problem", state)
        tmp = tempfile.mkdtemp(prefix="usage names state ")
        self.addCleanup(shutil.rmtree, tmp, True)
        with open(os.path.join(tmp, "state.json"), "w") as f:
            json.dump(state, f)
        with mock.patch.object(collect, "state_path", lambda: os.path.join(tmp, "state.json")):
            collect.note(names_problem=collect.project_names({"engine": "proj"})[1])
        with open(os.path.join(tmp, "state.json")) as f:
            self.assertNotIn("names_problem", json.load(f))


if __name__ == "__main__":
    unittest.main()
