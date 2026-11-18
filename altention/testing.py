"""A guard every engine test module calls first: tests never touch the real home.

    from altention import testing; testing.guard_home()   # at the top of a test module, before anything else

The first call points HOME and the XDG folders at a throwaway directory for the whole test run, and notes what is
in the real ~/.local/state/altention, ~/.cache/altention, ~/.config/altention and ~/altention. When the run ends,
anything new, gone or changed there makes the run fail (exit status 1), naming it: code that worked a path out
from the real home behind the tests' back (2026-10-06, a module-level ~ path) is caught the same day.

Files the real sync rewrites every round (it may run during the tests) are only checked for appearing, not for
changing; a plugin's own folder, ~/.local/state/altention/<its name>/, is not checked at all (a plugin that runs
here, like one serving pages, writes there whenever it likes: manager 2026-11-02). Which names those are comes from
the plugins this engine knows when the guard starts (altention.plugin.candidates: the built-in and the external
ones), never from a list written here; the core's own files and folders are checked as before, but for one a live
round rewrote meanwhile with altention/statefile.write_json (stamped "by_test": false): that one is said in a line
starting LIVE_LINE (deploy.sh shows it) and does not stop the run; stamped by a test, or not stamped, it does.
The instance's contents are not checked (the real sync pulls into it), only its top level. TMPDIR points into the
throwaway home too, so temporary folders a test does not remove are removed with it.
Standard library only, Python 3.8.
"""
import atexit
import json
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
           "cron-update.log", "cron-sync.log", "cron-watch.log", "cron-schedule.log",
           "remote-checks.json",  # C-25: the pre-push hook's cache (ops/remote.py), written when the real rounds push
           "update-mode",  # update.py: the mode last read, rewritten by a real round when it changes
           "deps-pending.lock"}  # C-40: update.py's and the supervisor's lock for the bot's packages (made once, kept)
# Not here on purpose (#code-review of 42a1663): deps-pending, update-available and instance-held. The real rounds
# make them rarely; the tests write them a lot, and a leaked deps-pending keeps the bot from restarting. A real round
# that makes or removes one during the tests is said once here, and the run is done again.
# 3a.11: what the scheduler's record-only round writes every minute in scheduler/ (names end in the machine's name),
# with its trace sampling (manager 2026-10-08)
RUNTIME_SCHEDULER = ("dry.", "files-dry.", "round-dry.", "said-dry.", "would-run.", "trace-history.", "trace-seen.",
                     "apply-report.")
# there only while a round samples: may come and go during the tests, never compared
PASSING_SCHEDULER = ("trace-sample.", "apply-reporting.")
_started = None
LIVE_LINE = "✓ 正式程序在測試期間改了"  # deploy.sh shows these lines of a run that passed


def plugin_names():
    """The names of the plugins this engine knows (their plugin.json "name", else their folder's), the core aside;
    none when they cannot be worked out (then every folder is checked, as before)."""
    try:
        from altention import plugin
        paths = plugin.candidates()
    except Exception:
        return set()
    names = set()
    for path in paths:
        if os.path.basename(path) != "plugin.json":
            continue  # altention/core.plugin.json: the core's
        try:
            with open(path, encoding="utf-8") as f:
                name = json.load(f).get("name")
        except (OSError, ValueError, AttributeError):
            name = None
        names.add(name if isinstance(name, str) and name else os.path.basename(os.path.dirname(path)))
    return names - {"", "core"}


def _snapshot(home, plugins=()):
    """plugins: the names whose folders in ~/.local/state/altention are not looked into (plugin_names())."""
    seen = {}
    for rel in (".local/state/altention", ".cache/altention", ".config/altention", ".local/share/altention"):  # (share:
        # dcremote-owner and bot-host, 11-08: a dcbot test left a real dcremote-owner)
        top = os.path.join(home, rel)
        for folder, dirs, files in os.walk(top):
            if folder == top and rel == ".local/state/altention":
                dirs[:] = [d for d in dirs if d not in plugins]
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
    try:  # the hooks of every Claude Code of the machine (dchook --install/--update) (manager 11-08)
        seen[os.path.join(home, ".claude", "settings.json")] = os.stat(os.path.join(home, ".claude", "settings.json")).st_mtime
    except OSError:
        pass
    for top in (os.path.join(home, "altention"), os.path.join(home, "altention", "instance")):
        try:
            for name in os.listdir(top):
                seen[os.path.join(top, name)] = None
        except OSError:
            pass
    return {k: v for k, v in seen.items() if not k.endswith(".tmp")}


def _check(home, before, plugins=()):
    after = _snapshot(home, plugins)
    new = sorted(set(after) - set(before))
    gone = sorted(set(before) - set(after))
    changed = sorted(k for k in set(after) & set(before) if before[k] is not None and after[k] != before[k])
    from altention import statefile  # standard library only, nothing read at import
    live = [k for k in new + changed if not k.endswith("/") and statefile.written_live(k)]
    for item in live:
        sys.stderr.write("%s：%s（不是測試寫的，放行）\n" % (LIVE_LINE, item))
    new, changed = [k for k in new if k not in live], [k for k in changed if k not in live]
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
    plugins = plugin_names()  # with the real home: the real instance and root
    before = _snapshot(home, plugins)
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
    atexit.register(_check, home, before, plugins)
    atexit.register(shutil.rmtree, fake, True)  # runs first (last registered): the throwaway home goes away
    _started = fake
    return fake


# C-148: golden tests, the fixed interfaces (spec chapters 10-17) held to files a person changes on purpose.
# golden(case, actual) compares `actual` (text, or a JSON value written as sorted, indented JSON) with
# <suite>/tests/golden/<case>.txt|.json, the folder beside the test calling it. It only ever reads that file: on a
# difference, or when there is none yet, the test fails with the diff and the one line that updates it (tools/golden
# update <case>), and with ALT_GOLDEN_OUT set (by tools/golden alone) the actual text is also left in that folder for
# the tool to copy. Lines at the top starting "#golden " are the file's header (the spec section it holds), not
# compared. Paths of the throwaway home and of the engine read <HOME> and <ENGINE>; times and ids are made fixed by
# the test itself, never wiped by a pattern here.
GOLDEN_HEAD = "#golden "


def shape(value, keep=()):
    """A JSON value as its form: an object's keys with the form of each value, a list as the forms of its items (once
    each, in order of appearance), and "str", "number", "bool", "null" for the rest; a key in `keep` keeps its
    value as it is (a version, a fixed word)."""
    if isinstance(value, dict):
        return {k: (v if k in keep else shape(v, keep)) for k, v in value.items()}
    if isinstance(value, list):
        out = []
        for x in value:
            s = shape(x, keep)
            if s not in out:
                out.append(s)
        return out
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "bool"
    return "number" if isinstance(value, (int, float)) else "str" if isinstance(value, str) else repr(type(value))


def golden_text(actual):
    text = actual if isinstance(actual, str) else json.dumps(actual, ensure_ascii=False, indent=1, sort_keys=True) + "\n"
    engine = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for path, word in ((os.environ.get("HOME", ""), "<HOME>"), (engine, "<ENGINE>")):
        if path and path != "/":
            text = text.replace(path, word)
    return text


def golden_path(case, folder):
    for ext in (".txt", ".json"):
        p = os.path.join(folder, "golden", case + ext)
        if os.path.exists(p):
            return p
    return None


def golden(case, actual, spec, folder=None):
    """Fail unless `actual` is what <folder>/golden/<case>.* holds (folder: the calling test's own). `spec`: the spec
    section, written in the header when tools/golden makes the file."""
    import difflib
    import inspect
    folder = folder or os.path.dirname(os.path.abspath(inspect.stack()[1].filename))
    text = golden_text(actual)
    path = golden_path(case, folder)
    out = os.environ.get("ALT_GOLDEN_OUT")
    if out:
        os.makedirs(out, exist_ok=True)
        with open(os.path.join(out, case.replace("/", "__")), "w", encoding="utf-8") as f:
            json.dump({"case": case, "folder": folder, "spec": spec,
                       "ext": ".txt" if isinstance(actual, str) else ".json", "text": text}, f, ensure_ascii=False)
    if path is None:
        raise AssertionError("golden %s 還沒有（spec %s）：確定這就是介面之後，跑 tools/golden update %s" % (case, spec, case))
    with open(path, encoding="utf-8") as f:
        want = "".join(line for i, line in enumerate(f) if not line.startswith(GOLDEN_HEAD))
    if want != text:
        diff = "".join(difflib.unified_diff(want.splitlines(True), text.splitlines(True), "golden", "now"))
        raise AssertionError("golden %s 變了（spec %s）：\n%s\n介面真的要改：tools/golden update %s，commit 訊息寫一行 "
                             "golden: %s — <為什麼>" % (case, spec, diff, case, case))
