"""State files the live rounds rewrite while tests may run (the bot's reconcile.json, update.py's updated-release),
written whole and stamped with whether a test wrote them (manager 2026-11-02).

    write_json(path, data)   data (a dict) plus "by_test", to a temporary file, then renamed over path
    written_live(path)       True when path holds a JSON object stamped "by_test": false

altention/testing.py lets such a file change in the real home during a test run when written_live() says so, and
says so; changed without the stamp, or stamped by a test, it still stops the run. A process is a test's when it has
TEST_SWITCH=1, which guard_home sets for the whole run and every process the tests start inherits.
Standard library only, Python 3.8.
"""
import json
import os

TEST_SWITCH = "ALT_TEST_ANY_ENGINE"
STAMP = "by_test"


def write_json(path, data):
    """data with STAMP, written whole: readers and the tests' guard never see half a file. OSError as open's."""
    folder = os.path.dirname(path)
    if folder:
        os.makedirs(folder, exist_ok=True)
    tmp = "%s.%d.tmp" % (path, os.getpid())  # its own: two writers at once never share one
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(dict(data, **{STAMP: os.environ.get(TEST_SWITCH) == "1"}), f, ensure_ascii=False)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def written_live(path):
    """Whether the file at path was last written by write_json outside the tests."""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return False
    return isinstance(data, dict) and data.get(STAMP) is False
