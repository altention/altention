"""The keys the bot sends, recorded against a baseline (3b 2.7: the typing decisions move into the connector, and
not one key may change on the way).

What is recorded: every key, every typing and every Escape that reaches a fake pane (tests/fakepane.py notes what
goes through its replacements of tmuxio.keys, type_text and escape), test by test, while the bot's own tests in
MODULES run: their screens are the samples (the fake screens test_safety and its neighbours were built from, and
the connector's real screen samples they read). The baseline is tests/samples/keys/baseline.json, written on main
84b69f8 before anything moved. tests/test_keys_same.py runs this and compares; a commit that changes keys on
purpose names each test whose keys differ, and why, in tests/samples/keys/changed.json.

    python -m tests.keylog --write   # write the baseline (only on the commit the baseline is of)
    python -m tests.keylog --check   # compare: exit status 1, and what differs, when anything does

Tests that replace tmuxio's functions themselves (mock.patch.object(tmuxio, ...)) instead of through the fake pane
are not seen; nor are tests that type nothing.
"""
import io
import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
BASELINE = os.path.join(HERE, "samples", "keys", "baseline.json")
CHANGED = os.path.join(HERE, "samples", "keys", "changed.json")
MODULES = ("test_safety", "test_parked", "test_btw_guard", "test_stream_wake", "test_standing", "test_messages", "test_open_session",
           "test_open_params", "test_screen_words", "test_whole_turn", "test_turn_end", "test_put_away", "test_screens", "test_panels",
           "test_resume_elsewhere", "test_archive", "test_menus", "test_awaiting")

# Tests whose waiting is timed by the clock, not by screens: how many times a key repeats there varies from run to
# run. Compared with each run of the same step counted as one (the steps and their order still must match).
UNSTEADY = {
    "tests.test_screen_words.StartupDialog.test_wait_ready_presses_nothing_on_a_resumed_conversation":
        "wait_ready() answers the screen every 5 ms until a SessionStart set 0.2 s later: 36 or 37 Down",
}

recorded = None  # test id -> [[what, target, args…], …] while recording
current = [None]


def kind(t):
    """A target as it is recorded: the bot's own session, or a pane (on this machine or another), without the
    numbers that differ from run to run."""
    if isinstance(t, dict):
        return "pane@far" if t.get("host") else "pane"
    return "session" if t else "none"


def said(arg):
    """An argument as it is recorded: text and numbers as they are; a function by its name (its repr bears an address
    that differs from run to run: escape()'s `needed`); anything else by its repr."""
    if isinstance(arg, (str, int, float)):
        return arg
    return getattr(arg, "__qualname__", None) or repr(arg)


def note(what, t, args):
    if recorded is not None and current[0]:
        recorded.setdefault(current[0], []).append([what, kind(t)] + [said(a) for a in args])


class Result(unittest.TextTestResult):
    def startTest(self, test):
        current[0] = test.id()
        super().startTest(test)

    def stopTest(self, test):
        super().stopTest(test)
        current[0] = None


def record():
    """Run MODULES and return what each test sent, and whether every test passed."""
    global recorded
    recorded = {}
    suite = unittest.TestSuite(unittest.defaultTestLoader.loadTestsFromName("tests." + m) for m in MODULES)
    out = io.StringIO()
    result = unittest.TextTestRunner(stream=out, resultclass=Result, verbosity=0).run(suite)
    got, recorded = recorded, None
    return got, result.wasSuccessful(), out.getvalue()


def runs(steps):
    """The steps with each run of the same step counted as one."""
    out = []
    for step in steps:
        if not out or out[-1] != step:
            out.append(step)
    return out


def differences(baseline, now, changed):
    """[(test id, what)]: every test whose keys are not the baseline's, but those named in `changed`."""
    out = []
    for test in sorted(set(baseline) | set(now)):
        if test in changed:
            continue
        if test not in now:
            out.append((test, "sends nothing now (baseline: %d)" % len(baseline[test])))
        elif test not in baseline:
            out.append((test, "sends %d now, nothing in the baseline (a new test: name it in changed.json)" % len(now[test])))
        elif test in UNSTEADY and runs(baseline[test]) == runs(now[test]):
            continue
        elif baseline[test] != now[test]:
            at = next((i for i, (a, b) in enumerate(zip(baseline[test], now[test])) if a != b), min(len(baseline[test]), len(now[test])))
            out.append((test, "differs from step %d: baseline %s, now %s" % (at, baseline[test][at:at + 3], now[test][at:at + 3])))
    return out


def main(argv):
    got, ok, text = record()
    if not ok:
        sys.stderr.write(text[-3000:])
        sys.stderr.write("\nkeylog: the tests themselves did not all pass (what follows is compared all the same)\n")
        if "--write" in argv:
            return 2  # a baseline is never written from a run with failures
    if "--write" in argv:
        os.makedirs(os.path.dirname(BASELINE), exist_ok=True)
        with open(BASELINE, "w", encoding="utf-8") as f:
            json.dump(got, f, ensure_ascii=False, indent=0, sort_keys=True)
        print("keylog: %d tests, %d sends written to %s" % (len(got), sum(len(v) for v in got.values()), BASELINE))
        return 0
    with open(BASELINE, encoding="utf-8") as f:
        baseline = json.load(f)
    try:
        with open(CHANGED, encoding="utf-8") as f:
            changed = json.load(f)
    except OSError:
        changed = {}
    diffs = differences(baseline, got, changed)
    for test, what in diffs:
        print("%s: %s" % (test, what))
    print("keylog: %d tests compared, %d differ, %d named as changed on purpose" % (len(set(baseline) | set(got)), len(diffs), len(changed)))
    return 1 if diffs else (0 if ok else 2)


if __name__ == "__main__":
    from tests import keylog  # the module the fake pane notes into (run as __main__, this file is another copy)
    sys.exit(keylog.main(sys.argv[1:]))
