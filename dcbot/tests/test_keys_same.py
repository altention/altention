"""3b 2.7: the typing decisions move into the connector, and not one key may change on the way. tests/keylog.py runs
the bot's own typing tests and records every key, typing and Escape that reaches the fake pane; here it is compared
with the baseline written on main 84b69f8 (tests/samples/keys/baseline.json). A commit that changes keys on purpose
names each test whose keys differ, and why, in tests/samples/keys/changed.json."""
import os
import subprocess
import sys
import unittest

from tests import keylog
from tests import tmuxguard  # noqa: F401,E402  (no test reaches a real tmux server)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class Comparison(unittest.TestCase):
    def test_runs_count_a_repeated_step_once(self):
        self.assertEqual(keylog.runs([["k", "s", "Down"]] * 3 + [["k", "s", "Enter"]]), [["k", "s", "Down"], ["k", "s", "Enter"]])

    def test_what_differs_is_named(self):
        base = {"a": [["keys", "session", "Enter"]], "b": [["type_text", "pane", "hi"]], "c": [["keys", "session", "C-u"]]}
        now = {"a": [["keys", "session", "Enter"]], "b": [["type_text", "pane", "hi"], ["keys", "pane", "Enter"]], "d": [["keys", "session", "x"]]}
        got = dict(keylog.differences(base, now, {}))
        self.assertEqual(sorted(got), ["b", "c", "d"])
        self.assertIn("differs from step 1", got["b"])
        self.assertIn("sends nothing now", got["c"])
        self.assertIn("a new test", got["d"])
        self.assertEqual(keylog.differences(base, now, {"b": "why", "c": "why", "d": "why"}), [])

    def test_a_clock_timed_test_may_repeat_a_step_more_or_fewer_times(self):
        test = next(iter(keylog.UNSTEADY))
        self.assertEqual(keylog.differences({test: [["keys", "session", "Down"]] * 37}, {test: [["keys", "session", "Down"]] * 36}, {}), [])
        self.assertNotEqual(keylog.differences({test: [["keys", "session", "Down"]]}, {test: [["keys", "session", "Up"]]}, {}), [])

    def test_targets_are_recorded_without_their_numbers(self):
        self.assertEqual([keylog.kind(t) for t in ("dc-x-1", {"id": "%3", "socket": "/s"}, {"id": "%3", "socket": "/s", "host": "lap"}, None)],
                         ["session", "pane", "pane@far", "none"])


class SameKeys(unittest.TestCase):
    def test_every_key_the_baseline_had(self):
        env = dict(os.environ, DCBOT_SUPERVISOR_TESTS="skip")
        r = subprocess.run([sys.executable, "-m", "tests.keylog", "--check"], cwd=ROOT, env=env, stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE, universal_newlines=True, timeout=900)
        report = [line for line in r.stdout.splitlines() if not line.startswith(("[", "stream "))]
        self.assertEqual(r.returncode, 0, "\n".join(report[-40:]) + "\n" + r.stderr[-2000:])
        self.assertIn("0 differ", report[-1])


if __name__ == "__main__":
    unittest.main()
