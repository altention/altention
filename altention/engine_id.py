#!/usr/bin/env python3
"""Is this checkout this machine's engine? Asked by everything that writes the machine's state: plugins.py apply
--write and restore, the install and uninstall scripts, instance.py's writing commands (the supervisor has its own
copy of the same rules, so that it never depends on code it may have to roll back).

  check(engine) -> ("official", here, "") | ("other", where this machine's engine is, why) | ("unknown", "", why)

"other": a worktree of another checkout (.git is a file), or this machine has its engine somewhere else (the machine
list's engine for it, ~/altention/engine, or where the altention command points). "unknown": it cannot be told (the machine list cannot be read; the checkout's .git, or one of those places, is
a link to somewhere not there: a share not mounted yet; a folder without .git that holds no engine). An engine
unpacked from an archive (no .git at all, its files there) is decided by the same places as a clone. Anything but "official" means: write nothing. A new machine
(none of those there yet) is "official". Without that, running a write from a worktree or a second clone added
every cron line and hook again and started a second bot on the same token (code-review 2026-10-06).

Tests: ALT_TEST_ANY_ENGINE=1 counts only while HOME is not the user's real home (altention/testing.py sets it with
the throwaway home), so no switch works in the real home.

  python3 altention/engine_id.py     # prints ALT-ENGINE-OK <path>, or why not (exit 1): for the shell scripts
Standard library only, Python 3.8.
"""
import os
import pwd
import sys

OK = "ALT-ENGINE-OK"


def test_override():
    if os.environ.get("ALT_TEST_ANY_ENGINE") != "1":
        return False
    try:
        real = os.path.realpath(pwd.getpwuid(os.getuid()).pw_dir)
    except KeyError:
        return False
    return os.path.realpath(os.path.expanduser("~")) != real


def main_checkout(dotgit_file):
    """The checkout a worktree belongs to, from its .git file ("gitdir: <main>/.git/worktrees/<name>"), or ""."""
    try:
        with open(dotgit_file) as f:
            line = f.readline().strip()
    except OSError:
        return ""
    if not line.startswith("gitdir:"):
        return ""
    gitdir = os.path.realpath(os.path.join(os.path.dirname(dotgit_file), line[7:].strip()))
    parts = gitdir.split(os.sep)
    if len(parts) > 2 and parts[-2] == "worktrees":
        return os.path.dirname(os.path.dirname(os.path.dirname(gitdir)))
    return os.path.dirname(gitdir)


POINTER = "~/.config/altention/engine"  # this machine's engine, written by the installer from that engine


def pointed():
    """The engine the pointer file names, or "" when there is none, it cannot be read, or it names a folder that is
    not there (then the places below decide: a broken pointer never refuses on its own)."""
    try:
        with open(os.path.expanduser(POINTER)) as f:
            path = os.path.expanduser(f.readline().strip())
    except (OSError, UnicodeDecodeError):
        return ""
    return path if path and os.path.isdir(path) else ""


def write_pointer(engine):
    """Write the pointer (the installer, once check() said official): its real path, atomically."""
    path = os.path.expanduser(POINTER)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path + ".tmp", "w") as f:
        f.write(os.path.realpath(engine) + "\n")
    os.replace(path + ".tmp", path)


def _decide(here, places, said, source=""):
    """The answer from the first of `places` that is there (a link to somewhere not there: unknown, a share not
    mounted yet), or None when none is; `said`: the list named it, so its absence is unknown too."""
    for place in places:
        if os.path.islink(place) and not os.path.exists(place):
            return ("unknown", "", "%s 指到讀不到的地方，判斷不了這份是不是這台的引擎" % place)
        if os.path.isdir(place):
            if os.path.realpath(place) == here:
                return ("official", here, "")
            return ("other", os.path.realpath(place), "這台的引擎在 %s（依據：%s），這份是 %s" % (
                os.path.realpath(place), source or place, here))
    if said:
        return ("unknown", "", "%s寫這台的引擎在 %s，但那裡讀不到" % (said, places[0]))
    return None


def check(engine):
    here = os.path.realpath(engine)
    if test_override():
        return ("official", here, "")
    dotgit = os.path.join(here, ".git")
    if os.path.isfile(dotgit):
        main = main_checkout(dotgit)
        return ("other", main, "%s 是 git worktree（屬於 %s），不是這台的引擎" % (here, main or "另一份 checkout"))
    if os.path.lexists(dotgit) and not os.path.isdir(dotgit):  # a link to somewhere not there: a share not mounted yet
        return ("unknown", "", "%s 讀不到，判斷不了這份是不是這台的引擎" % dotgit)
    if not os.path.lexists(dotgit) and not os.path.isfile(os.path.join(here, "altention", "engine_id.py")):
        return ("unknown", "", "%s 讀不到，判斷不了這份是不是這台的引擎" % dotgit)  # not an engine's folder (yet)
    # no .git but the engine's files: unpacked from an archive, decided by the places below like a clone
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    try:
        from altention import config
        root = config.root()
        m = (config.inventory().get("machines") or {}).get(config.this_machine())
    except Exception as e:  # a broken machine list: cannot tell where this machine's engine is
        return ("unknown", "", "機器清單讀不出來（%s），判斷不了這份是不是這台的引擎" % e)
    first = pointed()
    if first:  # the installer wrote where this machine's engine is: it alone decides (the supervisor reads it too)
        return _decide(here, [first], None, "指標檔 %s；搬引擎時要改它或刪掉它" % POINTER)
    named = m.get("engine") if isinstance(m, dict) and isinstance(m.get("engine"), str) and m["engine"].strip() else ""
    if named:  # the list says where: it alone decides (code-review 2026-10-06)
        return _decide(here, [os.path.expanduser(named)], "機器清單", "機器清單上這台的 engine")
    # else the default place decides when it is there; the altention command's link only when it is not
    found = _decide(here, [os.path.join(root, "engine")], None, "預設位置")
    if found:
        return found
    cmd = os.path.expanduser("~/.local/bin/altention")
    if os.path.islink(cmd):
        if not os.path.exists(cmd):
            return ("unknown", "", "%s 指到讀不到的地方，判斷不了這份是不是這台的引擎" % cmd)
        return _decide(here, [os.path.dirname(os.path.dirname(os.path.realpath(cmd)))], None,
                       "~/.local/bin/altention 指到那裡") or ("official", here, "")
    return ("official", here, "")


def refusal(engine, what):
    """"" when `engine` may write; else one line saying why `what` was not done."""
    kind, where, why = check(engine)
    if kind == "official":
        return ""
    if kind == "other":
        return "這不是這台的正式引擎：%s。%s沒做；請在 %s 跑" % (why, what, where)
    return "%s；%s沒做" % (why, what)


def warning(engine):
    """For read-only commands: a first line when this is not this machine's engine, else ""."""
    kind, where, why = check(engine)
    if kind == "official":
        return ""
    return "⚠ 這不是這台的正式引擎（%s）：下面看到的是這份 checkout 的結果" % (why if kind == "unknown" else "正式的在 %s" % where)


def main():
    engine = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    kind, where, why = check(engine)
    if kind == "official":
        print("%s %s" % (OK, where))
        return 0
    print("這不是這台的正式引擎：%s%s" % (why, "；請在 %s 跑" % where if where else ""))
    return 1


if __name__ == "__main__":
    sys.exit(main())
