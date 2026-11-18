"""altention/statefile.py and the tests' guard: a state file a live round rewrites while the tests run passes when
stamped by a live process; one a test wrote, a process a test started, or a write without the stamp stops the run
(manager 2026-11-02). update.py's twin writes the same."""
import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))))
from altention import testing as _testing  # noqa: E402
_testing.guard_home()  # tests never touch the real home (altention/testing.py)
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from altention import statefile, testing, update

ENGINE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class WriteJson(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="狀態 ")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.path = os.path.join(self.tmp, "a", "state.json")

    def read(self):
        with open(self.path, encoding="utf-8") as f:
            return json.load(f)

    def test_stamped_whole_and_nothing_left(self):
        with mock.patch.dict(os.environ, {statefile.TEST_SWITCH: "1"}):
            statefile.write_json(self.path, {"head": "x"})
        self.assertEqual(self.read(), {"head": "x", "by_test": True})
        self.assertFalse(statefile.written_live(self.path))
        with mock.patch.dict(os.environ):
            os.environ.pop(statefile.TEST_SWITCH)
            statefile.write_json(self.path, {"head": "y"})
        self.assertEqual(self.read(), {"head": "y", "by_test": False})
        self.assertTrue(statefile.written_live(self.path))
        self.assertEqual(os.listdir(os.path.dirname(self.path)), ["state.json"], "no temporary file left")

    def test_a_failed_write_leaves_the_old_file_and_no_temporary_one(self):
        statefile.write_json(self.path, {"head": "x"})
        with self.assertRaises(TypeError):
            statefile.write_json(self.path, {"head": object()})
        self.assertEqual(self.read()["head"], "x")
        self.assertEqual(os.listdir(os.path.dirname(self.path)), ["state.json"])

    def test_written_live_only_for_the_stamp_false(self):
        os.makedirs(os.path.dirname(self.path))
        for text, live in (('{"head": "x", "by_test": false}', True), ('{"head": "x"}', False),
                           ('{"head": "x", "by_test": 0}', False), ("[false]", False), ("{ half", False)):
            with self.subTest(text=text):
                with open(self.path, "w") as f:
                    f.write(text)
                self.assertEqual(statefile.written_live(self.path), live)
        self.assertFalse(statefile.written_live(os.path.join(self.tmp, "none.json")))

    def test_update_py_writes_the_same(self):
        """update.py imports no other module of the engine: its twin must write what write_json writes."""
        other = os.path.join(self.tmp, "b.json")
        for switch in ("1", None):
            with self.subTest(switch=switch), mock.patch.dict(os.environ):
                os.environ.pop(statefile.TEST_SWITCH, None)
                if switch:
                    os.environ[statefile.TEST_SWITCH] = switch
                statefile.write_json(self.path, {"tag": "v1", "at": 1.0})
                update.write_stamped(other, {"tag": "v1", "at": 1.0})
                with open(other) as f:
                    self.assertEqual(json.load(f), self.read())


GUARDED = r'''
import os, subprocess, sys, time
sys.path.insert(0, sys.argv[1])
from altention import statefile, testing
real = os.environ["HOME"]
path = os.path.join(real, ".local", "state", "altention", "rig-state.json")
testing.guard_home()
how = sys.argv[2]
if how == "live":  # another program's round, not started by the tests: its own environment
    env = dict(os.environ, HOME=real)
    env.pop(statefile.TEST_SWITCH)
    subprocess.run([sys.executable, "-c", "import sys; sys.path.insert(0, sys.argv[1]); from altention import statefile; "
                    "statefile.write_json(sys.argv[2], {'head': 'live'})", sys.argv[1], path], env=env, check=True, timeout=60)
elif how == "test":
    statefile.write_json(path, {"head": "test"})
elif how == "child":  # a "real" program a test started: it inherits the switch
    subprocess.run([sys.executable, "-c", "import sys; sys.path.insert(0, sys.argv[1]); from altention import statefile; "
                    "statefile.write_json(sys.argv[2], {'head': 'child'})", sys.argv[1], path], check=True, timeout=60)
elif how == "catalog":  # a bot restart's reconcile looks at the official list (catalog.look_before_asking)
    env = dict(os.environ, HOME=real)
    env.pop(statefile.TEST_SWITCH)
    subprocess.run([sys.executable, "-c", "import sys; sys.path.insert(0, sys.argv[1]); from altention import catalog; "
                    "catalog._save({'format': catalog.FORMAT, 'plugins': {}})", sys.argv[1]], env=env, check=True,
                   timeout=60)
elif how == "plain":
    with open(path, "w") as f:
        f.write('{"head": "plain"}')
'''


class Guard(unittest.TestCase):
    def run_guarded(self, how):
        home = tempfile.mkdtemp(prefix="家 ")
        self.addCleanup(shutil.rmtree, home, True)
        os.makedirs(os.path.join(home, ".local", "state", "altention"))
        with open(os.path.join(home, ".local", "state", "altention", "rig-state.json"), "w") as f:
            f.write('{"head": "before", "by_test": false}')
        env = dict(os.environ, HOME=home)
        for var in ("ALT_ROOT", "ALT_INSTANCE", statefile.TEST_SWITCH, "XDG_STATE_HOME", "XDG_CACHE_HOME",
                    "XDG_CONFIG_HOME"):
            env.pop(var, None)
        return subprocess.run([sys.executable, "-c", GUARDED, ENGINE, how], env=env, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, universal_newlines=True, timeout=120)

    def test_a_live_rounds_write_passes_and_is_said(self):
        r = self.run_guarded("live")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn(testing.LIVE_LINE, r.stderr)
        self.assertIn("rig-state.json", r.stderr)

    def test_the_official_list_a_bot_restart_looks_at_passes_too(self):
        """manager 11-03: the reconcile a restart runs rewrites ~/.local/state/altention/catalog.json."""
        r = self.run_guarded("catalog")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn(testing.LIVE_LINE, r.stderr)
        self.assertIn("catalog.json", r.stderr)

    def test_a_tests_write_stops_the_run(self):
        r = self.run_guarded("test")
        self.assertEqual(r.returncode, 1, r.stderr)
        self.assertIn("改過", r.stderr)
        self.assertNotIn(testing.LIVE_LINE, r.stderr)

    def test_a_program_a_test_started_stops_it_too(self):
        r = self.run_guarded("child")
        self.assertEqual(r.returncode, 1, r.stderr)
        self.assertNotIn(testing.LIVE_LINE, r.stderr)

    def test_a_write_without_the_stamp_stops_it(self):
        r = self.run_guarded("plain")
        self.assertEqual(r.returncode, 1, r.stderr)


if __name__ == "__main__":
    unittest.main()
