"""A guard every engine test module calls first: tests never touch the real home.

    from altention import testing; testing.guard_home()   # at the top of a test module, before anything else

The first call points HOME and the XDG folders at a throwaway directory for the whole test run, and notes what is
in the real ~/.local/state/altention, ~/.cache/altention, ~/.config/altention and ~/altention. When the run ends,
anything new, gone or changed there makes the run fail (exit status 1), naming it: code that worked a path out
from the real home behind the tests' back (2026-10-06, a module-level ~ path) is caught the same day.

Files the real sync rewrites every round (it may run during the tests) are only checked for appearing, not for
changing; the instance's contents are not checked (the real sync pulls into it), only its top level. TMPDIR points into
the throwaway home too, so temporary folders a test does not remove are removed with it.
Standard library only, Python 3.8.
"""
import atexit
import os
import shutil
import sys
import tempfile

RUNTIME = {"sync.json", "bot.json", "global.last", "global.lock", "handback.json", "allow-delete.json",
           # 3a.10: what the real sync, update.py and their launchers write every round (they may run during the tests).
           # So these files are outside this check: a test that writes one of them into the real home is not caught
           # here; reviewers compare them by hand (code-review 2026-10-07, item 4 accepted by the manager).
           "last_ok-sync", "last_ok-update", "last_alive-update", "last_skip-update", "alerts", "alerts-told.json",
           "watch-check-error.json",  # doctor --watch's own (the bot's machine): which machines cannot be checked
           "doctor-records.json",  # doctor --watch: each whole round's records, for the status boards
           "status-posted.json",  # the status plugin (the bot's machine): what its boards last said
           "cron-update.log", "cron-sync.log", "cron-watch.log", "cron-schedule.log"}
# 3a.11: what the scheduler's record-only round writes every minute in scheduler/ (names end in the machine's name),
# with its trace sampling (manager 2026-10-08)
RUNTIME_SCHEDULER = ("dry.", "files-dry.", "round-dry.", "said-dry.", "would-run.", "trace-history.", "trace-seen.",
                     "apply-report.")
# there only while a round samples: may come and go during the tests, never compared
PASSING_SCHEDULER = ("trace-sample.", "apply-reporting.")
_started = None


def _snapshot(home):
    seen = {}
    for rel in (".local/state/altention", ".cache/altention", ".config/altention"):
        top = os.path.join(home, rel)
        for folder, dirs, files in os.walk(top):
            for name in files:
                path = os.path.join(folder, name)
                base = name[:-4] if name.endswith(".tmp") else name
                if os.path.basename(folder) == "scheduler" and base.startswith(PASSING_SCHEDULER):
                    continue
                unrotated = base[:-2] if base.endswith(".1") else base
                runtime = unrotated in RUNTIME or (os.path.basename(folder) == "scheduler" and base.startswith(RUNTIME_SCHEDULER))
                if runtime and base != unrotated:
                    continue  # a log the real round rotated during the run
                try:
                    seen[path] = None if runtime else os.stat(path).st_mtime
                except OSError:
                    pass
            for name in dirs:
                seen[os.path.join(folder, name) + "/"] = None
    for top in (os.path.join(home, "altention"), os.path.join(home, "altention", "instance")):
        try:
            for name in os.listdir(top):
                seen[os.path.join(top, name)] = None
        except OSError:
            pass
    return {k: v for k, v in seen.items() if not k.endswith(".tmp")}


def _check(home, before):
    after = _snapshot(home)
    new = sorted(set(after) - set(before))
    gone = sorted(set(before) - set(after))
    changed = sorted(k for k in set(after) & set(before) if before[k] is not None and after[k] != before[k])
    if new or gone or changed:
        sys.stderr.write("\n✗ 測試動到了真的家目錄（%s）：\n" % home)
        for label, items in (("新增", new), ("不見", gone), ("改過", changed)):
            for item in items[:20]:
                sys.stderr.write("  %s：%s\n" % (label, item))
        sys.stderr.flush()
        os._exit(1)


def guard_home():
    global _started
    if _started is not None:
        return _started
    home = os.path.expanduser("~")
    before = _snapshot(home)
    fake = tempfile.mkdtemp(prefix="altention-test-home-")
    os.environ["HOME"] = fake
    os.environ["ALT_TEST_ANY_ENGINE"] = "1"  # engine_id: counts only because HOME is not the real home now
    for var, rel in (("XDG_STATE_HOME", ".local/state"), ("XDG_CACHE_HOME", ".cache"),
                     ("XDG_CONFIG_HOME", ".config"), ("XDG_DATA_HOME", ".local/share")):
        os.environ[var] = os.path.join(fake, rel)
    os.environ.pop("ALT_INSTANCE", None)
    os.environ["ALT_RUNTIME"] = os.path.join(fake, "runtime")  # the scheduler's marks beside the home: not /tmp's real one
    os.makedirs(os.path.join(fake, "tmp"))  # temporary folders the tests leave go away with the throwaway home
    os.environ["TMPDIR"] = os.path.join(fake, "tmp")
    tempfile.tempdir = None  # worked out again from TMPDIR
    atexit.register(_check, home, before)
    atexit.register(shutil.rmtree, fake, True)  # runs first (last registered): the throwaway home goes away
    _started = fake
    return fake
