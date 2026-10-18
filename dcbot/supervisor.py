#!/usr/bin/env python3
"""Keeps the bot running and rolls back bad versions. Standard library only, so a broken venv or a
bad bot commit can't take it down; nothing of the engine is imported here (the sync, when used, runs in a child
process). cron starts it every 5 minutes; a lock makes extra copies exit.

A "version" is the git tree of dcbot/ plus altention/ (the bot imports it): only a change there makes the next
start a trial. A version is "good" once it has been up for STABLE_SECS; while a good bot runs, last_good follows
HEAD as long as the version stays the same (commits elsewhere in the engine move it on), so a rollback takes back
only what came with the failed version. If a new version dies, never comes up within TRIAL_SECS, or sets aside
BAD_MIN or more events as failed during its trial (dcbot/state/events-bad/; those failed over a network or Discord
outage, *.net.json, do not count), the supervisor rolls back:
- the whole engine tree to last_good (a tree that ran together: altention/ is imported by bin/, ops/, chatlog/
  too), as a new commit whose message and notice list every commit taken back;
- only dcbot/ and altention/ ("partial": said so, for a person to sort out) when the whole tree cannot be put back
  without touching links or ignored paths: good..HEAD changes .gitignore/.gitattributes, or a path tracked in good
  is now a link, inside one, or ignored (a move into the instance in between), or uncommitted edits could not
  be saved.
Uncommitted edits (other channels share the work tree) to the files being put back are copied first to
<git dir>/altention-saved/<time>-rollback/ and listed in the notice; other edits and untracked files are left alone.
A rollback takes the git lock every automated job shares (waiting up to LOCK_WAIT, then going ahead anyway: a
broken bot must not wait on a stuck job). With the lock, the commit is put on top of the remote by
altention.sync.update in a child process (with a timeout); if that child cannot run at all, a plain rebase marked
as ours, aborted on conflict. Without the lock, only fetch, fast-forward and push. Every git call has a timeout,
and the bot is started whatever the network does. The sync never pushes the engine (C-31): an unpushed rollback
commit stays on this machine, said in the notice and by doctor's sync.engine-ahead, until pushed by hand.
All of that only when the instance's core.update is "branch" (update_mode, a copy of altention/update.py's): a
machine on releases (release, notify) keeps its rollback commit here, fetches, rebases and pushes nothing (the
remote's branch is not released, and its remote may not be ours to push to); the next release it updates to
leaves the commit behind (C-07; altention/sync.py local_commits).

Packages a new version needs and that are not installed yet (altention/update.py's deps-pending record: HEAD moved,
dcbot/requirements.txt changed, pip not done) are no sign of a bad version (C-40): before starting the bot while
that record is there, the supervisor runs pip itself (update.py's lock, the same command as dcbot/install.sh) and
starts the new version once it worked (the record then goes). Only if that pip failed and the new version then dies
in its trial is nothing rolled back (so nothing is committed, let alone pushed, in any core.update mode): the bot stays down, it is said
(notice, and altention dc notify once per head), and pip is tried again every DEPS_RETRY.

Changing this file is risky: the bot can't roll back its own supervisor. Keep it small. Rescue: SUPERVISOR-RESCUE.md.
"""
import fcntl
import json
import os
import shutil
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(ROOT, "state")
PY = os.path.join(ROOT, ".venv", "bin", "python")
ENGINE = os.path.dirname(ROOT)
PATHS = (".", "../altention")  # what a version is (relative to ROOT), and what a partial rollback puts back
TRIAL_SECS = 180
STABLE_SECS = 120
EXIT_RESTART = 42
LOCK_WAIT = 120
GIT_TIMEOUT = 60
NET_TIMEOUT = 60
SYNC_TIMEOUT = 120
DOWN_MAX = 170  # a rollback, lock wait included, keeps the bot down at most this long: the push can wait for the sync
POLL = 2
BACKOFF = 5
BAD_MIN = 2
FOLLOW_SECS = 30
DEPS_PIP_TIMEOUT = 300  # pip for missing packages before a start; the bot is down meanwhile
DEPS_LOCK_WAIT = 660  # update.py's own pip may hold its lock this long (its PIP_TIMEOUT and a minute)
DEPS_RETRY = 600  # packages that would not install: tried again this often, the bot down meanwhile
OURS = "agent-rebase"  # altention.sync's mark for a rebase that is ours to undo
REFUSED = 10  # the sync child's exit status when update() said no (a conflict...): not a reason for the fallback
SYNC_CHILD = """import os, sys
try:
    from altention import sync
except BaseException as e:
    print("import: %s" % e); sys.exit(3)
repo = sync.Repo(os.getcwd())
repo.lock_fd = int(sys.argv[1])
code, note = sync.update(repo)
if note:
    print(note)
sys.exit(10 if code else 0)
"""


def log(msg):
    print("%s %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg), flush=True)


def git(*args, timeout=GIT_TIMEOUT, strip=True):
    try:
        r = subprocess.run(["git", "-C", ROOT] + list(args), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           universal_newlines=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        log("git %s: no answer in %ds, given up" % (args[0], timeout))
        return 124, "", "git %s timed out" % args[0]
    return r.returncode, (r.stdout.strip() if strip else r.stdout), r.stderr.strip()


def names(*args):
    """The NUL-separated paths a git command (given -z) prints; [] on failure."""
    rc, out, _ = git(*args, strip=False)
    return [n for n in out.split("\0") if n] if rc == 0 else []


def read(name, default=""):
    try:
        with open(os.path.join(STATE, name)) as f:
            return f.read().strip()
    except OSError:
        return default


def write(name, text):
    with open(os.path.join(STATE, name), "w") as f:
        f.write(text)


def version(rev="HEAD"):
    """Tree hashes of dcbot/ and altention/ at `rev`: changes elsewhere in the repo don't count as a new version."""
    return "+".join(git("rev-parse", "%s:%s" % (rev, p if p != "." else "./"))[1] for p in PATHS)


def bot_tree(rev="HEAD"):
    """dcbot/'s tree alone: what the bot writes into state/ready once its self test passes."""
    return git("rev-parse", "%s:./" % rev)[1]


def tree(rev="HEAD"):
    return git("rev-parse", "%s^{tree}" % rev)[1]


def git_dir():
    gd = git("rev-parse", "--git-common-dir")[1]
    return gd if os.path.isabs(gd) else os.path.join(ROOT, gd)


def git_lock():
    """The shared git lock (<git dir>/altention-git.lock), waited for up to LOCK_WAIT seconds; None if not had."""
    try:
        f = open(os.path.join(git_dir(), "altention-git.lock"), "a")
    except OSError:
        return None
    until = time.time() + LOCK_WAIT
    while True:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return f
        except OSError:
            if time.time() > until:
                f.close()
                return None
            time.sleep(1)


def left(until, cap):
    """Seconds a network step may take: at most `cap`, and nothing past `until` (the bot is down meanwhile);
    0 when fewer than 5 seconds are left (not worth starting)."""
    remaining = until - time.time()
    return min(cap, remaining) if remaining >= 5 else 0


def run_child(top, lock, timeout):
    """altention.sync.update in a process of its own, from the altention/ just put back: (exit status or None, output).
    It runs in a session of its own; on a timeout the whole session is killed (git and ssh below it hold the lock
    too, and the sync's own wrapper moves git into another process group, not out of the session)."""
    try:
        child = subprocess.Popen([sys.executable, "-c", SYNC_CHILD, str(lock.fileno())], cwd=top,
                                 stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True,
                                 pass_fds=(lock.fileno(),), start_new_session=True)
    except OSError as e:
        return None, str(e)
    try:
        out = child.communicate(timeout=timeout)[0]
        return child.returncode, out.strip()
    except subprocess.TimeoutExpired:
        subprocess.run(["pkill", "-KILL", "-s", str(child.pid)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        child.kill()
        try:
            child.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            pass
        return None, "no answer in %ds" % timeout


def put_on_remote(lock, until):
    """The rollback commit on top of the remote, then pushed; True if pushed. Never raises, and nothing runs
    past `until`: a commit not pushed by then stays here until someone pushes it (the sync does not push the
    engine; doctor's sync.engine-ahead lists it)."""
    def push():
        return left(until, NET_TIMEOUT) > 0 and git("push", "-q", timeout=left(until, NET_TIMEOUT))[0] == 0
    if lock is None:  # someone else may be moving the branch: nothing that rewrites it
        if left(until, NET_TIMEOUT):
            git("fetch", "-q", timeout=left(until, NET_TIMEOUT))
            git("merge", "-q", "--ff-only", "@{u}")
        return push()
    if not left(until, SYNC_TIMEOUT):
        return False
    top = git("rev-parse", "--show-toplevel")[1]
    code, out = run_child(top, lock, left(until, SYNC_TIMEOUT))
    if code == 0:
        return push()
    log("rollback: sync update: %s%s" % ("exit %s: " % code if code is not None else "", out[-300:]))
    if code == REFUSED or not left(until, NET_TIMEOUT):  # refused (a conflict...), or no time: left for later
        return False
    mark = os.path.join(git_dir(), OURS)
    try:
        open(mark, "w").close()  # killed halfway: the sync knows this rebase is ours and undoes it
    except OSError as e:
        log("rollback: cannot write %s (%s); not rebasing" % (mark, e))
        return False
    rc, _, err = git("pull", "-q", "--rebase", "--autostash", timeout=left(until, NET_TIMEOUT))
    if rc != 0:  # a conflict or no network: the rollback commit stays here unpushed, not left mid-rebase
        log("rollback: pull --rebase: %s" % err[:300])
        git("rebase", "--abort")
    if not any(os.path.isdir(os.path.join(git_dir(), d)) for d in ("rebase-merge", "rebase-apply")):
        try:
            os.remove(mark)
        except OSError:
            pass
    return rc == 0 and push()


UPDATE_MODES = ("notify", "release", "branch")  # update.py's: only "branch" puts a rollback on the remote


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


def deps_path():
    """altention/update.py's deps-pending record (its state folder, from HOME at call time)."""
    return os.path.join(os.path.expanduser("~/.local/state/altention"), "deps-pending")


def deps_pending():
    """update.py's record ({head, why, at, next, tries}) while the bot's packages for a new HEAD are not installed,
    or None."""
    try:
        with open(deps_path()) as f:
            got = json.load(f)
        return got if isinstance(got, dict) else None
    except (OSError, ValueError):
        return None


def install_deps():
    """pip install -r requirements.txt into the bot's venv, as dcbot/install.sh and update.py do, under update.py's
    lock for it (waited for: update.py may be installing right now). "" when it worked or update.py did it meanwhile
    (the record is then gone), else why not. When it worked the record goes whatever HEAD it names: pip installed what
    the work tree asks for now (#code-review on e9622a9: a record of another HEAD left behind kept a broken version
    from being rolled back)."""
    try:
        lock = open(deps_path() + ".lock", "a")
    except OSError as e:
        return "拿不到套件安裝的鎖（%s）" % e
    try:
        until = time.time() + DEPS_LOCK_WAIT
        while True:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.time() > until:
                    return "update.py 正在裝套件，等了 %d 秒還沒結束" % DEPS_LOCK_WAIT
                time.sleep(BACKOFF)
        pending = deps_pending()
        if pending is None:
            return ""  # update.py installed them while we waited
        try:
            r = subprocess.run([PY, "-m", "pip", "install", "-q", "-r", "requirements.txt"], cwd=ROOT, stdin=subprocess.DEVNULL,
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True, errors="replace",
                               timeout=DEPS_PIP_TIMEOUT)
        except subprocess.TimeoutExpired:
            return "pip install %d 秒沒有結束" % DEPS_PIP_TIMEOUT
        except OSError as e:
            return "跑不了 pip（%s）" % e
        if r.returncode != 0:
            return "pip install 失敗（結束碼 %d）：%s" % (r.returncode, " ".join(r.stdout.split())[-300:])
        try:
            os.remove(deps_path())
        except OSError:
            pass
        return ""
    finally:
        lock.close()


def tell_once(key, text):
    """altention dc notify, once per `key` (the bot that would show a notice is down); best effort, never raises."""
    if read("told-deps") == key:
        return
    try:
        write("told-deps", key)
        subprocess.run([os.path.join(ENGINE, "bin", "altention"), "dc", "notify", text], stdin=subprocess.DEVNULL,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=NET_TIMEOUT)
    except Exception as e:
        log("could not notify (%r)" % e)


def failed_events_since(start):
    """Events the running version set aside as failed since `start` (dcbot/state/events-bad/), not counting those
    that failed over a network or Discord outage (*.net.json)."""
    bad = os.path.join(STATE, "events-bad")
    try:
        return [n for n in os.listdir(bad) if not n.endswith(".net.json")
                and os.path.getmtime(os.path.join(bad, n)) >= start]
    except OSError:
        return []


def notice(text):
    """Shown in Discord by the next bot that comes up."""
    prev = read("notice.txt")
    if text in prev:  # the same failure again on every retry: said once is enough
        return
    write("notice.txt", (prev + "\n" if prev else "") + text)
    log("NOTICE " + text)


def partial_reasons(good, top):
    """Why the whole tree cannot go back to `good` without touching links or ignored paths ([] if it can)."""
    changed = names("diff", "--name-only", "-z", "--no-renames", good, "HEAD", "--", ":/")
    why = [p for p in changed if os.path.basename(p) in (".gitignore", ".gitattributes")]
    gone = names("diff", "--name-only", "-z", "--no-renames", "--diff-filter=D", good, "HEAD", "--", ":/")
    for p in gone:  # tracked in good, not now: is it (or a folder on its way) a link, or ignored now?
        parts = p.split("/")
        if any(os.path.islink(os.path.join(top, *parts[:i])) for i in range(1, len(parts) + 1)):
            why.append(p)
    if gone:
        try:
            r = subprocess.run(["git", "-C", top, "check-ignore", "-z", "--stdin", "--no-index"], input="\0".join(gone),
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, timeout=GIT_TIMEOUT)
            why += [p for p in r.stdout.split("\0") if p]
        except (subprocess.TimeoutExpired, OSError) as e:  # cannot tell: the careful way
            why.append("（check-ignore 失敗：%s）" % e)
    return sorted(set(why))


def save_edits(paths, top):
    """Copy uncommitted edits to `paths` aside; (folder or None, ok). Raises nothing: ok False means not all saved."""
    if not paths:
        return None, True
    dest = os.path.join(git_dir(), "altention-saved", time.strftime("%Y%m%d-%H%M%S") + "-rollback")
    try:
        for p in paths:
            src = os.path.join(top, p)
            if os.path.lexists(src):
                os.makedirs(os.path.dirname(os.path.join(dest, p)), exist_ok=True)
                shutil.copy2(src, os.path.join(dest, p), follow_symlinks=False)
        return dest, True
    except OSError as e:
        log("rollback: saving edits failed: %s" % e)
        return dest, False


def rollback(good, reason):
    """Put the engine back to commit `good` as a new commit (the whole tree, or only dcbot/ and altention/ when the
    whole tree cannot go back cleanly). True if a rollback commit was made."""
    if not good:
        return False
    if git("cat-file", "-e", good + "^{commit}")[0] != 0:
        notice("⚠️ %s，但最後一個正常版本 `%s` 在 git 裡找不到（可能被改寫了），沒有退回；請人處理"
               "（照 dcbot/SUPERVISOR-RESCUE.md 看狀況，或手動 `git checkout <好的 commit> -- dcbot altention`）" % (reason, good[:7]))
        return False
    if tree(good) == tree():
        return False
    until = time.time() + DOWN_MAX
    lock = git_lock()
    if lock is None:
        log("rollback: the git lock was not had in %ds; going ahead" % LOCK_WAIT)
    try:
        return put_back(good, reason, lock, until)
    finally:
        if lock:
            lock.close()


def put_back(good, reason, lock, until):
    top = git("rev-parse", "--show-toplevel")[1]
    scope_partial = [":(top)" + os.path.relpath(os.path.normpath(os.path.join(ROOT, p)), top) for p in PATHS]
    why = partial_reasons(good, top)
    for attempt in ("whole", "partial"):
        if attempt == "whole" and why:
            continue
        spec = [":/"] if attempt == "whole" else scope_partial
        changed = names("diff", "--name-only", "-z", "--no-renames", good, "HEAD", "--", *spec)
        if not changed:
            return False
        overlap = sorted(set(names("diff", "--name-only", "-z", "--no-renames", "HEAD", "--", *spec)) & set(changed))
        saved, ok = save_edits(overlap, top)
        if not ok:
            if attempt == "whole":
                why = ["（沒提交的修改另存失敗）"]
                continue
            notice("⚠️ %s，但退回失敗：沒提交的修改另存失敗，沒有覆蓋它們（%s）" % (reason, "、".join(overlap[:10])))
            return False
        break
    undone = [l.split("\t", 1) for l in git("log", "--format=%h\t%s", "%s..HEAD" % good)[1].splitlines() if l]
    bot = set(git("log", "--format=%h", "%s..HEAD" % good, "--", *scope_partial)[1].split())
    added = set(names("diff", "--name-only", "-z", "--no-renames", "--diff-filter=A", good, "HEAD", "--", *spec))
    back = [p for p in changed if p not in added]
    for i in range(0, len(back), 200):
        git("checkout", good, "--", *[":(top)" + p for p in back[i:i + 200]])
    for i in range(0, len(sorted(added)), 200):
        git("rm", "-q", "-f", "--", *[":(top)" + p for p in sorted(added)[i:i + 200]])
    lines = ["%s %s%s" % (h, s, "" if h in bot else "（不是 dcbot/altention 的修改%s）" % ("，要重新提交" if attempt == "whole" else "，沒有被退"))
             for h, s in undone]
    if attempt == "partial":
        lines.insert(0, "部分退回：只退了 dcbot/ 和 altention/，引擎現在是沒有一起跑過的組合，需要人處理。原因：%s" % "、".join(why[:10]))
    rc, _, err = git("commit", "-q", "-m", "dcbot: roll back%s to %s (%s)" % (" (partial)" if attempt == "partial" else "", good[:7], reason),
                     "-m", "Taken back:\n" + "\n".join(lines) if lines else "", "--", *[":(top)" + p for p in changed])
    if rc != 0:
        notice("⚠️ %s，但退回失敗：%s" % (reason, err[:200]))
        return False
    same = tree() == tree(good) if attempt == "whole" else version() == version(good)
    if not same:
        log("rollback: the tree after the rollback is not %s's" % good[:7])
    try:
        mode = update_mode()[0]
    except Exception as e:  # never a reason not to roll back: then nothing on the remote, the careful way
        log("rollback: core.update not read (%r); nothing put on the remote" % e)
        mode = "notify"
    pushed = put_on_remote(lock, until) if mode == "branch" else None
    shown = lines[:15] + (["…還有 %d 個（git log 看）" % (len(lines) - 15)] if len(lines) > 15 else [])
    text = "⚠️ %s，已把%s退回 `%s`（壞掉的版本仍在 git 歷史裡）。被退掉的 commit：\n%s" % (
        reason, "整個引擎" if attempt == "whole" else " dcbot/ 和 altention/（部分退回）", good[:7], "\n".join(shown))
    if saved:
        text += ("\n別的頻道沒提交的修改（%s）另存在 `%s`：請自己把修改合併回去，不要整個複製回去"
                 "（另存的是整個檔，複製回去等於把退回撤掉）" % ("、".join(overlap[:10]), saved))
    if pushed is None:
        text += ("\n這台的 core.update 是 %s：退回的 commit 只留在這台，不推出去、也不拉上游的分支；"
                 "之後更新到新的發佈版本時它會被丟掉（新版取代它），不用推" % mode)
    elif not pushed:
        text += ("\n退回的 commit 還沒推出去（網路或衝突），同步不會替你推：在這台的引擎跑 `bin/home-git push`，"
                 "被拒的話先 `bin/home-pull` 再推（系統檢查會一直列著，直到推出去）；這台已經是退回後的版本")
    if not same:
        text += "\n（退回後的樹和 `%s` 不完全相同，請人看一下）" % good[:7]
    notice(text)
    return True


def safe_rollback(good, reason):
    """rollback() that never ends the supervisor: anything unexpected is said, and the bot is started as is.
    None when it failed that way (not False: "nothing to roll back to" would be wrong to say then)."""
    try:
        return rollback(good, reason)
    except Exception as e:
        log("rollback failed: %r" % e)
        try:
            notice("⚠️ %s，但退回時出錯（%s），沒有退回；請照 dcbot/SUPERVISOR-RESCUE.md 處理" % (reason, str(e)[:200]))
        except OSError:
            pass
        return None


ENGINE_WAIT, ENGINE_POLL = 300, 10  # at boot a home on NFS may not be there yet: wait this long before refusing


def engine_verdict():
    """("ok", "") this machine's engine; ("other", why) another checkout: never runs the bot; ("wait", why) a path
    cannot be read yet (a link into a share not mounted, the checkout's .git a link to nowhere, a folder without .git
    that holds no engine). An engine unpacked from an archive (no .git, its files there) is decided like a clone.
    The rules of altention/engine_id.py without the machine list (the supervisor never imports code it may have to
    roll back; ops tests feed both the same cases). Each checkout has its own lock, so a worktree or a second clone
    would otherwise start a second bot on the same token (manager 2026-10-06). ALT_TEST_ANY_ENGINE=1 counts only
    while HOME is not the real home (the tests)."""
    import pwd
    if os.environ.get("ALT_TEST_ANY_ENGINE") == "1":
        try:
            if os.path.realpath(os.path.expanduser("~")) != os.path.realpath(pwd.getpwuid(os.getuid()).pw_dir):
                return ("ok", "")
        except KeyError:
            pass
    here = os.path.realpath(ENGINE)
    dotgit = os.path.join(here, ".git")
    if os.path.isfile(dotgit):
        return ("other", "%s 是 git worktree，不是這台的引擎" % here)
    if os.path.lexists(dotgit) and not os.path.isdir(dotgit):  # a link to somewhere not there
        return ("wait", "%s 讀不到（還沒掛好？）" % dotgit)
    if not os.path.lexists(dotgit) and not os.path.isfile(os.path.join(here, "altention", "engine_id.py")):
        return ("wait", "%s 讀不到（還沒掛好？）" % dotgit)  # not an engine's folder (yet)
    # no .git but the engine's files: unpacked from an archive, decided by the places below like a clone
    try:  # the installer's pointer (altention/engine_id.py POINTER) first: it covers an engine the list names elsewhere
        with open(os.path.expanduser("~/.config/altention/engine")) as f:
            named = os.path.expanduser(f.readline().strip())
    except (OSError, UnicodeDecodeError):
        named = ""
    if named and os.path.isdir(named):  # unreadable or naming nothing: as if there were none
        return ("ok", "") if os.path.realpath(named) == here else \
            ("other", "這台的引擎在 %s，這份是 %s" % (os.path.realpath(named), here))
    root = os.path.expanduser(os.environ.get("ALT_ROOT") or "~/altention")
    # then the default place decides when it is there; the altention command's link only when it is not
    for place in (os.path.join(root, "engine"),):
        if os.path.islink(place) and not os.path.exists(place):
            return ("wait", "%s 指到讀不到的地方（還沒掛好？）" % place)
        if os.path.isdir(place):
            return ("ok", "") if os.path.realpath(place) == here else \
                ("other", "這台的引擎在 %s，這份是 %s" % (os.path.realpath(place), here))
    cmd = os.path.expanduser("~/.local/bin/altention")
    if os.path.islink(cmd):
        if not os.path.exists(cmd):
            return ("wait", "%s 指到讀不到的地方（還沒掛好？）" % cmd)
        other = os.path.dirname(os.path.dirname(os.path.realpath(cmd)))
        if os.path.realpath(other) != here:
            return ("other", "這台的引擎在 %s，這份是 %s" % (os.path.realpath(other), here))
    return ("ok", "")


def not_this_machines_engine(wait=ENGINE_WAIT):
    """"" to go on; else why not. A path that cannot be read yet is waited for (up to `wait` seconds, said in the
    log) and only then refused: a slow mount at boot must not leave the bot down for good."""
    deadline = time.time() + wait
    while True:
        kind, why = engine_verdict()
        if kind == "ok":
            return ""
        if kind == "other" or time.time() >= deadline:
            return why if kind == "other" else "判斷不了這份是不是這台的引擎（%s，等了 %d 秒）" % (why, wait)
        try:
            log("engine check: %s; trying again in %ds" % (why, ENGINE_POLL))
        except OSError:
            pass
        time.sleep(ENGINE_POLL)


def main():
    if sys.argv[1:] == ["--check-engine"]:  # the identity check alone, nothing started: for after a deploy
        kind, why = engine_verdict()
        print("%s %s" % (kind, why or os.path.realpath(ENGINE)))
        return 0 if kind == "ok" else 1
    why = not_this_machines_engine()
    if why:  # before the state folder, the lock or anything else of this checkout
        print("supervisor：不啟動。這不是這台的正式引擎：%s" % why)
        return 1
    os.makedirs(STATE, exist_ok=True)
    lock = open(os.path.join(STATE, "supervisor.lock"), "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return 0  # already running
    write("supervisor.pid", str(os.getpid()))
    backoff = BACKOFF
    while True:
        if os.path.exists(os.path.join(STATE, "stop")):
            log("stop flag present; exiting")
            return 0
        if os.path.exists(os.path.join(STATE, "rollback-requested")):
            os.remove(os.path.join(STATE, "rollback-requested"))
            if safe_rollback(read("last_good"), "使用者要求退回") is False:
                notice("沒有可以退回的版本（目前就是最後一個正常版本）")
        head = git("rev-parse", "HEAD")[1]
        ver, btree = version(), bot_tree()
        good = read("last_good")
        trial = not good or version(good) != ver
        start = time.time()
        try:
            os.remove(os.path.join(STATE, "ready"))
        except OSError:
            pass
        deps_why = None
        if deps_pending() is not None and os.path.exists(PY):  # C-40: the new version's packages first
            log("packages for %s pending (update.py's deps-pending); installing before the start" % head[:7])
            deps_why = install_deps()
            log("packages: %s" % (deps_why or "installed"))
        log("starting bot %s%s" % (head[:7], " (trial)" if trial else ""))
        proc = subprocess.Popen([PY, "-m", "bot.main"], cwd=ROOT)
        write("bot.pid", str(proc.pid))
        marked, followed = False, 0
        while proc.poll() is None:
            time.sleep(POLL)
            up = time.time() - start
            ready = read("ready").startswith(btree)
            if trial and not marked and len(failed_events_since(start)) >= BAD_MIN:
                log("version %s set events aside as failed during its trial; killing" % head[:7])
                proc.kill()
                break
            if not marked and up > STABLE_SECS and ready:
                write("last_good", head)
                marked, backoff, followed = True, BACKOFF, time.time()
                log("version %s is good" % head[:7])
            if marked and time.time() - followed > FOLLOW_SECS:  # commits elsewhere: last_good follows HEAD
                followed, now = time.time(), git("rev-parse", "HEAD")[1]
                if now and now != read("last_good") and version(now) == ver:
                    write("last_good", now)
            if trial and not marked and up > TRIAL_SECS and not ready:
                log("version %s never came up; killing" % head[:7])
                proc.kill()
        proc.wait()
        code = proc.returncode
        up = time.time() - start
        log("bot exited with %s after %ds" % (code, up))
        if code == EXIT_RESTART:
            continue  # deploy or !restart: start whatever is checked out now
        if trial and not marked and deps_why:
            # pip failed before this start: its packages are not installed, no sign the version is bad, nothing rolled
            # back (C-40). Only then: a pip that worked, or none tried, and a failure is the version's own
            text = ("新版本 `%s` 啟動不了，它要的套件還沒裝上（%s）：這不算新版壞掉，沒有退回。每 %d 分鐘會再裝一次；"
                    "要現在處理：在這台的引擎跑 dcbot/install.sh" % (head[:7], deps_why, DEPS_RETRY // 60))
            notice(text)
            tell_once(head, "⚠️ 機器人停著：" + text)
            time.sleep(DEPS_RETRY)
            continue
        if trial and not marked:
            if safe_rollback(good, "新版本 `%s` 啟動失敗（exit %s）" % (head[:7], code)):
                continue
        time.sleep(backoff)  # crash of a good version: retry with backoff
        backoff = min(backoff * 2, 300)


if __name__ == "__main__":
    sys.exit(main())
