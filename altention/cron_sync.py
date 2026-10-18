#!/usr/bin/env python3
"""The cron entry of the engine and instance sync (3a.10): runs one round of `python3 -m altention.sync`; when that
cannot run to its end (it does not load, it throws, it takes too long), fast-forwards the engine by plain git and
writes why into the alerts file itself. Standard library only, nothing of the engine imported, as short as it can
be: the one thing here a broken sync.py cannot take down (manager 2026-10-06). Twin of altention/cron_update.py."""
import fcntl
import json
import os
import signal
import subprocess
import sys
import time

ENGINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DONE = "ALT-SYNC-DONE"  # python3 -m altention.sync prints it when a round came to its end


def add_alert(rows, stamp, source, kind, text, keep=200):
    """The alerts file's rows with this one added: one source and kind keeps one row, the newest, counted (an
    earlier one is taken out), so kinds repeating, even in turn, never push the others out; then the last `keep`. The same in update.py,
    sync.py and both cron launchers (manager 2026-10-07)."""
    text = " ".join(str(text).split())
    for i in range(len(rows) - 1, -1, -1):  # an earlier row of the same source and kind: this one takes its place
        row = rows[i].rstrip("\n").split("\t", 3)
        if len(row) == 4 and row[1] == source and row[2] == kind:
            head, mark, count = row[3].rpartition("（共 ")
            n = int(count[:-3]) + 1 if mark and count.endswith(" 次）") and count[:-3].isdigit() else 2
            text, rows = "%s（共 %d 次）" % (text, n), rows[:i] + rows[i + 1:]
            break
    return (rows + ["%s\t%s\t%s\t%s\n" % (stamp, source, kind, text)])[-keep:]


def write_alert(source, kind, text):
    """add_alert() into ~/.local/state/altention/alerts under its lock; never raises."""
    try:
        folder = os.path.expanduser("~/.local/state/altention")
        os.makedirs(folder, exist_ok=True)
        with open(os.path.join(folder, "alerts"), "a+", errors="replace") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            f.seek(0)
            rows = add_alert(f.readlines(), time.strftime("%Y-%m-%dT%H:%M:%S%z"), source, kind, text)
            f.seek(0)
            f.truncate()
            f.writelines(rows)
    except OSError:
        pass


def note(text, kind="fallback"):
    """One line in the alerts file (write_alert)."""
    write_alert("cron-sync", kind, text)


def elsewhere():
    """Why this checkout is not this machine's engine, or "" when it is (a worktree; or the installer's pointer, else
    ~/altention/engine when it is there, names another): then nothing is run or written. The rules of
    altention/engine_id.py, short (the agreement test holds them together)."""
    import pwd
    if os.environ.get("ALT_TEST_ANY_ENGINE") == "1":  # the tests' switch: only outside the real home
        try:
            if os.path.realpath(os.path.expanduser("~")) != os.path.realpath(pwd.getpwuid(os.getuid()).pw_dir):
                return ""
        except KeyError:
            pass
    here = os.path.realpath(ENGINE)
    if os.path.isfile(os.path.join(here, ".git")):
        return "%s 是 git worktree，不是這台的引擎" % here
    try:
        with open(os.path.expanduser("~/.config/altention/engine")) as f:
            named = os.path.expanduser(f.readline().strip())
    except (OSError, UnicodeDecodeError):
        named = ""
    root = os.path.expanduser(os.environ.get("ALT_ROOT") or "~/altention")
    for place, source in ([(named, "指標檔 ~/.config/altention/engine")] if named and os.path.isdir(named) else []) + [
            (os.path.join(root, "engine"), "預設位置")]:
        if os.path.isdir(place):
            return "" if os.path.realpath(place) == here else "這台的引擎在 %s（依據：%s），這份是 %s" % (
                os.path.realpath(place), source, here)
    return ""


def refused():
    """The upstream hash update.py is refusing (its last_alive-update, plain JSON), or "": the same reading as
    sync.refused_hash() and update.blocked_hash(). No file: no record; unreadable: no record, and written down."""
    import json
    path = os.path.expanduser("~/.local/state/altention/last_alive-update")
    try:
        with open(path) as f:
            got = json.load(f)
    except FileNotFoundError:
        return ""
    except (OSError, ValueError) as e:
        note("讀不出 %s（%s），當成沒有被擋的紀錄" % (path, e), "alive-unreadable")
        return ""
    if not isinstance(got, dict):  # [] or a bare value: no record either, and said like an unreadable one
        note("%s 不是物件，當成沒有被擋的紀錄" % path, "alive-unreadable")
        return ""
    b = got.get("blocked")
    return b.get("hash", "") if isinstance(b, dict) and isinstance(b.get("hash"), str) else ""


def stacked(h):
    """Is `h` in the upstream's history, below it, and not yet in HEAD? (sync.stacked_on, by plain git.)"""
    def under(a, b):
        return subprocess.run(["git", "-C", ENGINE, "merge-base", "--is-ancestor", a, b], stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL, timeout=60).returncode == 0
    return under(h, "@{u}") and not under(h, "HEAD")


UPDATE_MODES = ("notify", "release", "branch")  # update.py's: the plain fast-forward runs only for "branch"


def update_mode():
    """(mode, problem): the instance's core.update, read the way altention/config.py finds the instance (the same
    reading is copied into cron_update.py, cron_sync.py, sync.py and dcbot/supervisor.py; the agreement test holds
    them together).
    plugins.json read and the key not set: "notify". No instance or no plugins.json to be seen (a share not mounted
    yet, a machine that left its instance): the mode last read. The settings cannot be read, or the value is none of
    UPDATE_MODES: the mode last read too, and `problem` says why: a comma too many in plugins.json must not stop a
    machine that follows its branch from getting fixes. The mode last read is update-mode (written by update.py),
    "notify" when there is none, and then `problem` says that too (C-04)."""
    def last(problem="", unseen=""):
        """The mode last read, with `problem`; none (or not one of UPDATE_MODES): notify, and why, `unseen` being
        what was not seen (C-04: a machine that never read its settings is told it does not update by itself)."""
        try:
            with open(os.path.expanduser("~/.local/state/altention/update-mode")) as f:
                m = json.load(f).get("mode")
        except (OSError, ValueError, AttributeError):
            m = None
        if m in UPDATE_MODES:
            return m, problem
        return "notify", problem or "%s，也沒有上次讀到的模式：照 notify，不自動更新" % (unseen or "上次讀到的模式不能用")
    named = os.environ.get("ALT_INSTANCE")
    pointer = os.path.expanduser("~/.config/altention/instance")
    if not named and os.path.exists(pointer):
        try:
            with open(pointer) as f:
                named = f.readline().strip()
        except (OSError, UnicodeDecodeError) as e:
            return last("讀不了 %s（%s）" % (pointer, e))
    if named == "-":  # this machine has left its instance
        return last(unseen="這台已經離開實例")
    if named:
        inst = os.path.expanduser(named)
        if not os.path.isdir(inst):
            return last("實例資料夾不存在：%s" % inst)
    else:
        inst = os.path.join(os.path.expanduser(os.environ.get("ALT_ROOT") or "~/altention"), "instance")
        if not os.path.isdir(inst):
            return last(unseen="看不到實例（%s）" % inst)
    path = os.path.join(inst, "plugins.json")
    try:
        with open(path, encoding="utf-8") as f:
            got = json.load(f)
    except FileNotFoundError:
        return last(unseen="實例裡沒有 %s" % path)
    except (OSError, ValueError, UnicodeDecodeError) as e:
        return last("讀不出 %s（%s）" % (path, e))
    core = got.get("core", {}) if isinstance(got, dict) else None
    if not isinstance(core, dict):
        return last("%s 的%s不是物件" % (path, " core" if isinstance(got, dict) else "內容"))
    mode = core.get("update", "notify")
    if mode not in UPDATE_MODES:
        return last("%s 的 core.update 是 %s，只能是 notify、release 或 branch" % (path, json.dumps(mode, ensure_ascii=False)[:40]))
    return mode, ""


def plain_ff():
    """git fetch and merge --ff-only under the repo's lock; nothing when it is taken or a rebase is underway, nor when
    the instance's core.update is not "branch" (notify and release never take the branch: manager 2026-10-14)."""
    mode, _ = update_mode()
    if mode != "branch":
        return "沒做（core.update 是 %s：保底快轉只在 branch 模式拉上游分支）" % mode
    gd = os.path.join(ENGINE, ".git")
    if not os.path.isdir(gd) or any(os.path.exists(os.path.join(gd, n)) for n in ("rebase-merge", "rebase-apply", "index.lock")):
        return "沒做（不是 git checkout，或 rebase 進行中）"
    try:
        lock = open(os.path.join(gd, "altention-git.lock"), "a")
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return "沒做（鎖被拿著）"
    with lock:
        for args in (["fetch", "-q"], ["merge", "--ff-only", "-q", "@{u}"]):
            if args[0] == "merge" and refused() and refused() == subprocess.run(
                    ["git", "-C", ENGINE, "rev-parse", "@{u}"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                    universal_newlines=True, timeout=60).stdout.strip():
                return "沒做（上游 %s 被 update.py 擋下，保底也不拉）" % refused()[:12]
            if args[0] == "merge" and refused() and stacked(refused()):  # nobody checking: pulled, never quietly
                note("update 沒在檢查，拉進了疊在被擋的 %s 上面的 %s，沒有檢查" % (refused()[:12], subprocess.run(
                    ["git", "-C", ENGINE, "rev-parse", "--short=12", "@{u}"], stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL, universal_newlines=True, timeout=60).stdout.strip()), "stacked-on-refused")
            try:
                r = subprocess.run(["git", "-C", ENGINE] + args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   universal_newlines=True, timeout=300, stdin=subprocess.DEVNULL)
            except (OSError, subprocess.SubprocessError) as e:
                return "git %s 出錯（%s）" % (args[0], e)
            if r.returncode != 0:
                return "git %s 失敗：%s" % (args[0], r.stdout.strip()[:200])
    return "快轉好了"


LOG_KEPT = 500  # the cron line appends this file's output to ~/.local/state/altention/cron-sync.log


def trim_log():
    """The cron line's log, cut to its last LOG_KEPT lines before this round adds to it (never fails the round)."""
    path = os.path.expanduser("~/.local/state/altention/cron-sync.log")
    try:
        with open(path, "r+", errors="replace") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            rows = f.readlines()
            if len(rows) > LOG_KEPT:
                f.seek(0)
                f.truncate()
                f.writelines(rows[-LOG_KEPT:])
    except OSError:
        pass


def say(text):
    """One line for the cron line's log: when, and what happened."""
    print("%s %s" % (time.strftime("%Y-%m-%dT%H:%M:%S%z"), " ".join(str(text).split())), flush=True)


def main():
    why = elsewhere()
    if why:  # another checkout: nothing run, nothing written but this line on stdout (the cron line keeps it)
        say("不是這台的正式引擎：%s；什麼都沒做" % why)
        return 0
    trim_log()
    try:
        return round_or_fallback()
    except BaseException as e:  # the one thing this file must never miss: the fallback (manager 2026-10-06)
        if isinstance(e, KeyboardInterrupt):
            raise
        note("啟動檔自己出錯（%s: %s）；改用單純的快轉：%s" % (type(e).__name__, e, plain_ff()))
        return 1


def round_or_fallback():
    try:
        p = subprocess.Popen([sys.executable, "-m", "altention.sync"], cwd=ENGINE, stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, universal_newlines=True, errors="replace",
                             stdin=subprocess.DEVNULL, start_new_session=True)
        out, _ = p.communicate(timeout=900)
        if DONE in out.splitlines():
            say("這一輪跑完（結束碼 %s）%s" % (p.returncode, "：" + out.strip().splitlines()[-2][:200]
                                          if len(out.strip().splitlines()) > 1 else ""))
            return p.returncode
        why = "同步沒跑完（結束碼 %s）：%s" % (p.returncode, out.strip()[-200:])
    except subprocess.TimeoutExpired:
        os.killpg(p.pid, signal.SIGKILL)
        p.wait()
        why = "同步 900 秒沒有結束，已中止"
    except OSError as e:
        why = "起不了同步（%s）" % e
    result = plain_ff()
    note("%s；改用單純的快轉：%s" % (why, result))
    say("沒跑完，保底：%s；%s" % (result, why))
    return 1


if __name__ == "__main__":
    sys.exit(main())
