#!/usr/bin/env python3
"""Keeps the bot running and rolls back bad versions. Standard library only, so a broken venv or a
bad bot commit can't take it down; nothing of the engine is imported here (the sync, when used, runs in a child
process). cron starts it every 5 minutes; a lock makes extra copies exit.

  python3 dcbot/supervisor.py                  what cron runs (every 5 minutes, and at boot): keep the bot running
  python3 dcbot/supervisor.py --check-engine   only say whether this is this machine's engine; starts nothing (after a deploy)
  python3 dcbot/supervisor.py -h | --help      this text

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
Which last_good (manager 11-19, the 11-18 rollback): this machine's moves only while it runs the bot, so a standby
taking over goes by the engine the other machine's bot ran when that is newer (dcbot/state-standby/meta.json
"engine": own last_good in its history, itself in HEAD's; anything else, own last_good alone: good_here()). It
decides a trial and what a rollback goes back to.
Too far to roll back (manager 11-19): a new version more than HOLD_COMMITS commits, HOLD_LINES lines or HOLD_FILES
files (in dcbot/ and altention/) from last_good that fails its trial is not rolled back: state/rollback-held.json
holds that head, which then runs as a good version would (crash backoff, no trial), the owner is told once (with
!rollback, and touch dcbot/state/rollback-requested for a bot that is down), each later down one line mentioning
nobody. The hold goes when that head is marked good or HEAD moves (a deploy, !rollback).
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
that record is there, or while the work tree's requirements.txt is not what pip last installed (state/venv-requirements,
a hash written on every pip that worked; however HEAD got where it is: a step back taken out by hand, a rebase, a reset),
the supervisor runs pip itself (update.py's lock, the same command as dcbot/install.sh) and
starts the new version once it worked (the record then goes). Only if that pip failed and the new version then dies
in its trial is nothing rolled back (so nothing is committed, let alone pushed, in any core.update mode): the bot stays down, it is said
(notice, and altention dc notify once per head), and pip is tried again every DEPS_RETRY.

Once pip for that head has kept failing STEP_BACK_AFTER (C-44; dcbot/state/deps-failing.json), the supervisor steps back
to last_good the way it rolls back, under a subject of its own ("dcbot: step back to <good> while packages install
(<new>)"), and pushes it in no mode: the bot comes back on the old version (dcbot/state/stepped-back.json). Moving
forward is the supervisor's alone (update.py's install_pending leaves a step back alone; the sync drops such a commit
when it moves to a newer upstream): every DEPS_RETRY while the old version runs, pip installs the new version's
requirements and, under the same pip lock, the branch is reset (--keep) to it and the bot restarted into its trial.
When that move is refused (overlapping edits, the git lock not had), pip at once puts back what the old version's
requirements ask, so a restart of the old version finds its packages; when that pip fails too, the venv is "mixed"
and an old version that will not start leads straight to the move forward. A rollback that changes
requirements.txt runs pip for the tree put back.

Changing this file is risky: the bot can't roll back its own supervisor. Keep it small. Rescue: SUPERVISOR-RESCUE.md.
"""
import fcntl
import hashlib
import importlib.util
import json
import os
import re
import shutil
import socket
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
EXIT_LEASE = 4     # C-163: the bot gave the place up (another machine holds it, or it could not tell): not a crash
LEASE_WAIT = 60    # how often the lease is looked at again while another machine holds it
LEASE_GATE_SECS = 120  # (dclease keeps itself under 100)
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
STEP_BACK_AFTER = 1800  # pip for one new version failing this long: back to last_good on this machine meanwhile (C-44)
STEPPED, FAILING, INSTALLED = "stepped-back.json", "deps-failing.json", "venv-requirements"
# a new version this far from last_good (in dcbot/ and altention/) that will not start is not rolled back on its own:
# taking back that much on one failed start does more harm than a bot down a while (the 11-18 rollback, manager 11-19)
HOLD_COMMITS, HOLD_LINES, HOLD_FILES = 20, 3000, 60
HELD = "rollback-held.json"  # in STATE: {"head", "good", "size", "at", "downs"}: that head runs on without a trial
STANDBY_META = ("state-standby", "meta.json")  # (under ROOT) what the other machine's bot sent last: its "engine" HEAD
# the subject put_back gives a step back (altention/sync.py and update.py read it with the same pattern; a test holds them)
STEP_BACK_SUBJECT = re.compile(r"^dcbot: step back to ([0-9a-f]{7}) while packages install \([0-9a-f]{7}\)$")
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


def steps_below():
    """C-44 (manager 2026-10-17, #code-review of 60c6eec): commits a push would send (@{u}..HEAD) with a step back's
    subject, the subject alone (more held back rather than less): short hashes; [] with no upstream."""
    rc, out, _ = git("log", "--format=%h%x00%s", "@{u}..HEAD")
    return [l.split("\0")[0] for l in out.splitlines() if rc == 0 and STEP_BACK_SUBJECT.match((l.split("\0") + [""])[1])]


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


def deps_lock():
    """update.py's lock for pip, waited for up to DEPS_LOCK_WAIT (update.py may be installing right now):
    (open file, "") or (None, why not)."""
    try:
        lock = open(deps_path() + ".lock", "a")
    except OSError as e:
        return None, "拿不到套件安裝的鎖（%s）" % e
    until = time.time() + DEPS_LOCK_WAIT
    while True:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return lock, ""
        except OSError:
            if time.time() > until:
                lock.close()
                return None, "update.py 正在裝套件，等了 %d 秒還沒結束" % DEPS_LOCK_WAIT
            time.sleep(BACKOFF)


def pip(req="requirements.txt"):
    """pip install -q -r `req` (from ROOT) into the bot's venv, as dcbot/install.sh and update.py do: "" or why not."""
    try:
        r = subprocess.run([PY, "-m", "pip", "install", "-q", "-r", req], cwd=ROOT, stdin=subprocess.DEVNULL,
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True, errors="replace",
                           timeout=DEPS_PIP_TIMEOUT)
    except subprocess.TimeoutExpired:
        return "pip install %d 秒沒有結束" % DEPS_PIP_TIMEOUT
    except OSError as e:
        return "跑不了 pip（%s）" % e
    if r.returncode != 0:
        return "pip install 失敗（結束碼 %d）：%s" % (r.returncode, " ".join(r.stdout.split())[-300:])
    try:  # what the venv now holds (C-44: compared before every start, however HEAD got where it is)
        write(INSTALLED, reqs_hash(os.path.join(ROOT, req)))
    except OSError as e:
        log("cannot write %s (%s)" % (INSTALLED, e))
    return ""


def reqs_hash(path=None):
    """sha256 of a requirements file (the work tree's by default); "" when there is none."""
    try:
        with open(path or os.path.join(ROOT, "requirements.txt"), "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()
    except OSError:
        return ""


def reqs_changed():
    """The work tree's requirements.txt is not what pip last installed into the venv (state/venv-requirements, written
    on every pip that worked; none yet counts as changed): pip before the start, as for deps-pending (manager
    2026-10-17, #code-review-2 of 60c6eec: a step back taken out by hand left nothing that said so). No
    requirements.txt: nothing to install."""
    now = reqs_hash()
    return bool(now) and now != read(INSTALLED)


def install_deps():
    """pip install -r requirements.txt into the bot's venv under update.py's lock for it. "" when it worked or
    update.py did it meanwhile (the record is then gone), else why not. When it worked the record goes whatever HEAD
    it names: pip installed what the work tree asks for now (#code-review on e9622a9: a record of another HEAD left
    behind kept a broken version from being rolled back)."""
    lock, why = deps_lock()
    if lock is None:
        return why
    try:
        if deps_pending() is None and not reqs_changed():
            return ""  # installed while we waited
        why = pip()
        if why:
            return why
        try:
            os.remove(deps_path())
        except OSError:
            pass
        return ""
    finally:
        lock.close()


def read_json(name):
    """A dict this supervisor wrote in state/, or None."""
    try:
        with open(os.path.join(STATE, name)) as f:
            got = json.load(f)
        return got if isinstance(got, dict) else None
    except (OSError, ValueError):
        return None


def write_json(name, data):
    try:
        with open(os.path.join(STATE, name + ".tmp"), "w") as f:
            json.dump(data, f)
        os.replace(os.path.join(STATE, name + ".tmp"), os.path.join(STATE, name))
    except OSError as e:
        log("cannot write %s (%s)" % (name, e))


def drop(*paths):
    """Remove files of state/ (or absolute paths); gone already is fine."""
    for p in paths:
        try:
            os.remove(os.path.join(STATE, p))
        except OSError:
            pass


def failing_since(head):
    """C-44: when pip for `head` first failed (state/deps-failing.json, started over for another head), this failure
    counted."""
    rec = read_json(FAILING) or {}
    same = rec.get("head") == head and type(rec.get("since")) in (int, float)
    tries = rec.get("tries") if same and type(rec.get("tries")) is int else 0
    since = rec["since"] if same else time.time()
    write_json(FAILING, {"head": head, "since": since, "tries": tries + 1})
    return since


def step_back(good, target, why):
    """C-44: pip for the new version `target` kept failing: back to `good` here, under the step-back subject, pushed in
    no mode; the record that lets the supervisor move forward again. True if stepped back."""
    if not safe_rollback(good, "新版本 `%s` 要的套件一直裝不上（%s）" % (target[:7], why), step=target):
        return False
    commit, parent = git("rev-parse", "HEAD")[1], git("rev-parse", "HEAD^")[1]
    write_json(STEPPED, {"target": parent, "commit": commit, "good": good, "since": time.time(), "venv": ""})
    drop(FAILING)
    tell_once("step:" + parent, "⚠️ 機器人先用舊版回來了：新版 `%s` 要的套件裝不上（%s），先退回 `%s`；套件裝好後會自己回到新版。"
              "這個退回只在這台，不要推。要現在處理：在這台的引擎跑 dcbot/install.sh" % (parent[:7], why, good[:7]))
    return True


def step_forward(rec, running):
    """C-44: stepped back (`rec`, state/stepped-back.json): pip for the new version's requirements, then at once (same
    pip lock) the branch reset --keep to it, the records gone and, when the bot runs (`running`), the restart flag.
    "" when moved forward; "gone" when HEAD is no longer the step-back commit (the sync dropped it, or someone moved
    it: the record goes, nothing reset); else why not. A move refused after pip worked puts the old version's
    packages back (pip of the work tree's requirements); that failing too marks the venv "mixed"."""
    target, commit = str(rec.get("target") or ""), str(rec.get("commit") or "")
    if not target or not commit:
        drop(STEPPED)
        return "gone"
    if git("rev-parse", "HEAD")[1] != commit:
        return moved_on(commit)
    lock, why = deps_lock()
    if lock is None:
        return why
    try:
        rc, text, err = git("show", "%s:./requirements.txt" % target, strip=False)
        if rc != 0:
            return "讀不到新版 `%s` 的 requirements.txt（%s）" % (target[:7], err[:200])
        write("requirements-target.txt", text)
        why = pip(os.path.join(STATE, "requirements-target.txt"))
        if why:
            return why
        moved = forward(target, commit)
        if not moved:
            drop(STEPPED, FAILING, deps_path())
            if running:
                write("restart-requested", '{"channel": ""}\n')  # as deploy.sh and update.py write it
            log("packages for %s installed; moved forward to it" % target[:7])
            return ""
        back = pip()  # the old version runs on: its packages back as its requirements ask
        if moved == "gone":
            return moved_on(commit)
        rec.update(venv="mixed" if back else "", blocked=moved)
        write_json(STEPPED, rec)
        if back:
            text = ("新版 `%s` 的套件裝好了，但回到新版沒成功（%s），把套件裝回舊版要的版本也失敗（%s）：舊版重啟時可能起不來，"
                    "起不來時會直接試回到新版。要現在處理：在這台的引擎跑 dcbot/install.sh" % (target[:7], moved, back))
            notice("⚠️ " + text)
            tell_once("mixed:" + target, "⚠️ " + text)
        else:
            notice("新版 `%s` 的套件裝好了，但回到新版沒成功（%s）：已經把套件裝回舊版要的版本，舊版照常跑，%d 分鐘後再試"
                   % (target[:7], moved, DEPS_RETRY // 60))
        return "回到新版沒成功（%s）" % moved
    finally:
        lock.close()


def left_behind():
    """The step-back commit of state/stepped-back.json, or None. Its records go once that commit is not in HEAD's
    history (the sync or update.py left it behind on moving on; #code-review-2 of 88e7229: a trial never got to
    step_forward, and doctor told of a step back long gone); still in it (commits on top) they stay."""
    commit = (read_json(STEPPED) or {}).get("commit")
    if commit and git("merge-base", "--is-ancestor", str(commit), "HEAD")[0] == 1:
        log("the step back %s is no longer in HEAD's history: its records go" % str(commit)[:7])
        drop(STEPPED, FAILING, "requirements-target.txt")
        return None
    return commit


def moved_on(commit):
    """HEAD is no longer the step-back commit `commit`. Not in its history (the sync left it behind, or someone took
    it away): the record goes, "gone". Still in it, commits on top (a deploy, a commit by hand; #code-review of
    88e7229): the record stays (doctor keeps telling), said once, nothing moved; why. Not known: kept, why."""
    rc = git("merge-base", "--is-ancestor", commit, "HEAD")[0]
    if rc == 1:
        drop(STEPPED, FAILING, "requirements-target.txt")
        return "gone"
    if rc != 0:
        return "看不出退路 commit `%s` 還在不在歷史裡" % commit[:7]
    notice("⚠️ 退路 commit `%s`（套件裝不上時先用舊版跑）上面疊了別的 commit，supervisor 不會自己回到新版。請人處理："
           "在引擎跑 `bin/home-git rebase --onto %s^ %s` 拿掉退路（dcbot/SUPERVISOR-RESCUE.md），之後交給 supervisor；都不要推"
           % (commit[:7], commit[:7], commit[:7]))
    return "退路 commit `%s` 上面疊了別的 commit，要人處理" % commit[:7]


def forward(target, commit):
    """reset --keep from the step-back commit to `target`, under the git lock: "" moved; "gone" HEAD is not that
    commit any more (moved_on tells which way); else why not (nothing moved)."""
    gl = git_lock()
    if gl is None:
        return "拿不到 git 鎖（等了 %d 秒）" % LOCK_WAIT
    try:
        if git("rev-parse", "HEAD")[1] != commit:
            return "gone"
        if git("rev-parse", commit + "^")[1] != target:
            return "退路 commit 的上一個不是 `%s`" % target[:7]
        rc, _, err = git("reset", "-q", "--keep", target)
        return "工作樹有跟新版重疊的修改（%s）" % err[:200] if rc != 0 else ""
    finally:
        gl.close()


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


def other_engine():
    """The version the other machine's supervisor last marked good (its state/last_good, sent with the bot's state:
    dcbot/state-standby/meta.json "last_good", bot/standby.py meta()); "" when there is none or it cannot be read.
    Never the "engine" sent with it: that is its HEAD when sent, maybe a version on trial or merged and not yet run
    (#code-review on fbe1cfa6: trial protection gone for a version no bot ever ran)."""
    try:
        with open(os.path.join(ROOT, *STANDBY_META)) as f:
            got = json.load(f)
        engine = got.get("last_good") if isinstance(got, dict) else None
    except (OSError, ValueError):
        return ""
    return engine if isinstance(engine, str) and re.fullmatch(r"[0-9a-f]{40}", engine) else ""


def good_here():
    """The version to go by (trial or not, what a rollback goes back to): this machine's last_good, or the version the
    other machine marked good (other_engine()) when that is newer: own last_good in its history, and itself in HEAD's.
    last_good moves only while this machine runs the bot, so a standby that last ran it days ago would take back
    everything the primary ran since (the 11-18 rollback, manager 11-19). Anything not plain (no file, not a commit
    here, not in HEAD's history, own last_good not in its history): own last_good alone, no guessing."""
    own = read("last_good")
    other = other_engine()
    if not other or other == own or git("merge-base", "--is-ancestor", other, "HEAD")[0] != 0:
        return own
    if own and git("merge-base", "--is-ancestor", own, other)[0] != 0:
        return own
    if _going_by.get("said") != (other, own):  # (once per pair: it is asked twice a round, every restart too)
        _going_by["said"] = (other, own)
        log("last_good: the other machine marked %s good, newer than this machine's %s: going by it" % (other[:7], own[:7] or "(none)"))
    return other


_going_by = {}  # good_here()'s last line said


def change_size(good):
    """(commits, lines, files) in dcbot/ and altention/ from `good` to HEAD; None when git cannot tell."""
    rc, count, _ = git("rev-list", "--count", "%s..HEAD" % good, "--", *PATHS)
    rc2, stat, _ = git("diff", "--numstat", "-z", "--no-renames", good, "HEAD", "--", *PATHS, strip=False)
    if rc or rc2 or not count.isdigit():
        return None
    lines, files = 0, 0
    for entry in stat.split("\0"):
        parts = entry.split("\t")
        if len(parts) == 3:
            files += 1
            lines += sum(int(n) for n in parts[:2] if n.isdigit())  # ("-" for a binary file)
    return int(count), lines, files


def too_far(good):
    """(commits, lines, files) when HEAD is too far from `good` to roll back on its own (HOLD_*), else None (also
    when it cannot be told: rolled back as before)."""
    size = change_size(good) if good else None
    if size and (size[0] > HOLD_COMMITS or size[1] > HOLD_LINES or size[2] > HOLD_FILES):
        return size
    return None


def held_for(head):
    """state/rollback-held.json when it is about `head`; one about another head goes (a new version, a rollback)."""
    rec = read_json(HELD)
    if rec and rec.get("head") != head:
        drop(HELD)
        return None
    return rec


HELD_WHY = {  # why the trial ended, as hold() says it (#code-review-2 on fbe1cfa6, low 1: a version killed for its
    # failed events was said "啟動失敗（exit -9）")
    "exit": ("rollback_held_exit", "啟動失敗（exit {exit}）"),
    "events": ("rollback_held_events", "試用時把 {n} 個事件標成失敗，被停掉"),
    "never_up": ("rollback_held_never_up", "{secs} 秒內沒上線，被停掉"),
}


def hold(head, good, size, code, why="exit", n=0):
    """A new version too far from `good` did not start: not rolled back; it runs on as a good one would (crash
    backoff, no trial), and the owner is told once, with how to roll back. `why`: how its trial ended (HELD_WHY:
    "exit" on its own, killed for "events" it set aside as failed (`n` of them), or "never_up")."""
    write_json(HELD, {"head": head, "good": good, "size": list(size), "at": time.time(), "downs": 1})
    key, default = HELD_WHY.get(why) or HELD_WHY["exit"]
    how = said_words(key, default, exit=code, n=n, secs=TRIAL_SECS)
    text = said_words("rollback_held", "🛠️ 機器人新版 `{head}` {how}，但它跟上一個正常版本 `{good}` 差了 "
                      "{commits} 個 commit、{lines} 行、{files} 個檔（dcbot/、altention/），太多了，沒有自動退回，照常重試。"
                      "要退回：在 Discord 打 `!rollback`，或在 {machine} 的引擎 `touch dcbot/state/rollback-requested`",
                      head=head[:7], how=how, good=good[:7], commits=size[0], lines=size[1], files=size[2],
                      machine=this_machine())
    log("version %s did not start but is %d commits, %d lines, %d files from %s: not rolled back" % (
        head[:7], size[0], size[1], size[2], good[:7]))
    notice(text)
    ping(text)


def held_nap(secs):
    """The crash backoff of a held version: cut short by a rollback asked for (the way the owner was told, while the
    bot is down) or the stop flag, so neither waits up to the 300 s the backoff grows to."""
    until = time.time() + secs
    while time.time() < until:
        if os.path.exists(os.path.join(STATE, "rollback-requested")) or os.path.exists(os.path.join(STATE, "stop")):
            return
        time.sleep(min(POLL, max(0, until - time.time())))


def held_down(head, rec, code):
    """The held version went down again: one more line, mentioning nobody."""
    rec["downs"] = int(rec.get("downs") or 1) + 1
    write_json(HELD, rec)
    say_quietly(said_words("rollback_held_again", "🛠️ 新版 `{head}` 又停了（exit {exit}，第 {n} 次），照常重試；要退回打 `!rollback`",
                           head=head[:7], exit=code, n=rec["downs"]))


CRASH_WINDOW, CRASH_COUNT = 15 * 60, 3  # a good version down this many times within this long, never up STABLE_SECS
# in between: the bot keeps restarting, and the owner is told once (C-103)
RESTART_LOOP = "restart-loop"  # in STATE: the restart loop said and not over yet (the time it was said)


_loaded = {}


def bot_module(name):
    """dcbot/bot/<name>.py of this checkout, loaded by its path under a name of its own: nothing called `bot` is put in
    sys.modules (a test's fake bot package, or another checkout's, is never taken, nor left behind for others).
    bot/messages.py imports the engine's altention.text, so the engine is put on the path."""
    if name not in _loaded:
        if ENGINE not in sys.path:
            sys.path.insert(0, ENGINE)
        spec = importlib.util.spec_from_file_location("supervisor_bot_" + name, os.path.join(ROOT, "bot", name + ".py"))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _loaded[name] = module
    return _loaded[name]


def said_words(code, default, **data):
    """A line of the bot's words (bot/messages.py CALLS), as the installation words it; `default` when they cannot be
    read: what tells the owner the bot is down never depends on the bot's own code loading."""
    try:
        return bot_module("messages").call(code, **data)
    except Exception:
        return default.format(**data)


def as_program_env():
    """The environment the supervisor's dcctl send runs in: said to be a program's (ALTENTION_AS_PROGRAM), whoever
    started the supervisor (cron, or by hand under a Claude Code): its notices never get a Claude's 「來自」 line
    (C-172a P1d, manager 11-17). Whoever sets it becomes a program (no such line, edits by its key); all run under
    one account, so this is not proof of anything (C-186 P0-2)."""
    return dict(os.environ, ALTENTION_AS_PROGRAM="1")


def ping(text):
    """Straight to Discord (dcctl send, not through the bot, which may be down), in the default notification channel,
    mentioning the owner at the end (C-103). Best effort, never raises; True when it was sent."""
    try:
        owner = bot_module("envfile").load().get("OWNER_ID") or ""
        head, _, rest = text.partition("\n")
        body = ("%s <@%s>" % (head, owner) if owner.isdigit() else head) + ("\n" + rest if rest else "")
        r = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "dcctl"), "send", "@notify", body, "--mentions"],
                           stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=NET_TIMEOUT,
                           env=as_program_env())
        if r.returncode != 0:
            log("could not tell the owner (dcctl send %s): %s" % (r.returncode, r.stderr.decode("utf-8", "replace")[-200:]))
        return r.returncode == 0
    except Exception as e:
        log("could not tell the owner (%r)" % e)
        return False


def this_machine():
    """This machine's name in the machine list (altention.config), its host name when that cannot be read."""
    try:
        if ENGINE not in sys.path:
            sys.path.insert(0, ENGINE)
        from altention import config as altconfig
        return altconfig.this_machine() or socket.gethostname()
    except Exception:
        return socket.gethostname()


def say_quietly(text):
    """As ping(), mentioning nobody: the line that says what was said is over."""
    try:
        r = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "dcctl"), "send", "@notify", text],
                           stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=NET_TIMEOUT,
                           env=as_program_env())
        return r.returncode == 0
    except Exception as e:
        log("could not post (%r)" % e)
        return False


def restart_loop(crashes, now=None):
    """`crashes`: when the good version went down lately (never up STABLE_SECS since); kept to CRASH_WINDOW. The
    owner is told once when there are CRASH_COUNT of them, until the bot is up and stable again (restart_over())."""
    now = time.time() if now is None else now
    crashes[:] = [t for t in crashes if now - t <= CRASH_WINDOW]
    if len(crashes) < CRASH_COUNT or read(RESTART_LOOP):
        return False
    write(RESTART_LOOP, str(int(now)))
    machine = this_machine()
    where = said_words("restarting_where", "原因在 {machine} 的 {path}", machine=machine,
                       path=os.path.join(STATE, "supervisor.out"))
    log("the bot keeps restarting (%d times in %d minutes): telling the owner" % (len(crashes), CRASH_WINDOW // 60))
    ping(said_words("restarting", "🛠️ 機器人一直重啟，沒辦法上線") + "\n" + where)
    return True


def restart_over():
    """The bot is up and stable: a restart loop said is over (said, mentioning nobody); the next one is told again."""
    if not read(RESTART_LOOP):
        return False
    try:
        os.remove(os.path.join(STATE, RESTART_LOOP))
    except OSError:
        pass
    say_quietly(said_words("restarting_back", "✅ 機器人恢復了"))
    return True


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


def rollback(good, reason, step=""):
    """Put the engine back to commit `good` as a new commit (the whole tree, or only dcbot/ and altention/ when the
    whole tree cannot go back cleanly). True if a rollback commit was made. `step`: the new version whose packages
    would not install (C-44): the step-back subject, nothing put on the remote."""
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
        return put_back(good, reason, lock, until, step)
    finally:
        if lock:
            lock.close()


def put_back(good, reason, lock, until, step=""):
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
    subject = ("dcbot: step back to %s while packages install (%s)" % (good[:7], step[:7]) if step else
               "dcbot: roll back%s to %s (%s)" % (" (partial)" if attempt == "partial" else "", good[:7], reason))
    rc, _, err = git("commit", "-q", "-m", subject,
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
    below = steps_below() if mode == "branch" and not step else []
    pushed = put_on_remote(lock, until) if mode == "branch" and not step and not below else None
    shown = lines[:15] + (["…還有 %d 個（git log 看）" % (len(lines) - 15)] if len(lines) > 15 else [])
    scope = "整個引擎" if attempt == "whole" else " dcbot/ 和 altention/（部分退回）"
    if step:
        text = ("⚠️ %s，先把%s退回 `%s` 用舊版跑；套件裝好後會自己回到新版。這個退回只在這台，不要推。"
                "要現在處理：在這台的引擎跑 dcbot/install.sh。暫時退掉的 commit：\n%s" % (reason, scope, good[:7], "\n".join(shown)))
    else:
        text = "⚠️ %s，已把%s退回 `%s`（壞掉的版本仍在 git 歷史裡）。被退掉的 commit：\n%s" % (reason, scope, good[:7], "\n".join(shown))
    if saved:
        text += ("\n別的頻道沒提交的修改（%s）另存在 `%s`：請自己把修改合併回去，不要整個複製回去"
                 "（另存的是整個檔，複製回去等於把退回撤掉）" % ("、".join(overlap[:10]), saved))
    if step:
        pass  # pushed in no mode, said above
    elif below:  # rolled back here all the same; a step back must never reach the upstream
        text += ("\n退路 commit（%s，套件裝不上時先用舊版跑）壓在下面，沒推：先在引擎跑 `bin/home-git rebase --onto %s^ %s` 拿掉退路"
                 "（dcbot/SUPERVISOR-RESCUE.md），再推自己的 commit，不要直接推" % ("、".join(below), below[-1], below[-1]))
    elif pushed is None:
        text += ("\n這台的 core.update 是 %s：退回的 commit 只留在這台，不推出去、也不拉上游的分支；"
                 "之後更新到新的發佈版本時它會被丟掉（新版取代它），不用推" % mode)
    elif not pushed:
        text += ("\n退回的 commit 還沒推出去（網路或衝突），同步不會替你推：在這台的引擎跑 `bin/home-git push`，"
                 "被拒的話先 `bin/home-pull` 再推（系統檢查會一直列著，直到推出去）；這台已經是退回後的版本")
    if not same:
        text += "\n（退回後的樹和 `%s` 不完全相同，請人看一下）" % good[:7]
    req = os.path.relpath(os.path.join(ROOT, "requirements.txt"), top)
    if not step and req in changed and os.path.exists(PY):  # the venv as the tree put back asks (C-44; install.sh's way)
        lock, why = deps_lock()
        if lock:
            try:
                why = pip()
            finally:
                lock.close()
        log("rollback: packages for the tree put back: %s" % (why or "installed"))
        if why:
            text += "\n退回後的套件沒裝上（%s）：在這台的引擎跑 dcbot/install.sh" % why
    notice(text)
    return True


def safe_rollback(good, reason, step=""):
    """rollback() that never ends the supervisor: anything unexpected is said, and the bot is started as is.
    None when it failed that way (not False: "nothing to roll back to" would be wrong to say then)."""
    try:
        return rollback(good, reason, step)
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


def token_there():
    """Is the bot's token on this machine? altention.config.bot_token_there(): the same answer install gives.
    Not known (the engine's config not importable): True, as before (the gate decides)."""
    try:
        import bot  # noqa: F401  (puts the engine's folder on the path)
        from altention import config as engine_config
        return engine_config.bot_token_there()
    except Exception:
        return True


def standby_here():
    """Is this machine a standby: the machine list gives it the standby role; only when the list cannot be read,
    what the gate said last (state/lease-role: a standby without its token never runs the gate, so that file may be
    old; #code-review-2 on c1780a4). Not known: False (the gate decides, as before)."""
    try:
        import bot  # noqa: F401
        from altention import config as engine_config
        machines = engine_config.inventory().get("machines")
        if machines:
            me = engine_config.this_machine()
            return "dcbot-standby" in ((machines.get(me) or {}).get("roles") or [])
    except Exception:
        pass
    return read("lease-role") == "standby"


GATE_SAID = None  # what the gate last said on stderr with its "wait" (lease_gate(): logged when it changes)


def lease_gate(crashing=False):
    """C-163: tools/dclease gate, before the bot starts: "start" or "wait" (another machine holds the place, or this
    standby has no reason to take it). `crashing`: the bot went down lately before it was ever up STABLE_SECS (said
    to the gate: a lapsed place is then left to the standby, lease.leave_to_standby()). On a standby
    (state/lease-role) only its 0 starts the bot: a gate killed, hung or broken is "wait" (#code-review-2 on
    ccd8baa). On the primary its own trouble is "start" (dclease itself says 10 for what must wait: Discord out of
    reach, a fence). Why it says "wait", when it says (its stderr), is logged once a change (drill problem B)."""
    tool = os.path.join(ROOT, "tools", "dclease")
    standby = read("lease-role") == "standby"  # (dclease writes this machine's part first thing: #code-review-2)
    if not os.path.exists(tool) or not os.path.exists(PY):
        return "wait" if standby else "start"
    try:
        r = subprocess.run([PY, tool, "gate"] + (["--crashing"] if crashing else []), cwd=ROOT, capture_output=True,
                           timeout=LEASE_GATE_SECS)
        code = r.returncode
    except (OSError, subprocess.TimeoutExpired) as e:
        code = e.__class__.__name__
    global GATE_SAID
    if code == 0:
        GATE_SAID = None
        return "start"
    standby = read("lease-role") == "standby"
    if code == 10:  # why, when it says (dclease's said(), its trouble): logged once a change (drill problem B)
        got = (r.stderr or b"").decode("utf-8", "replace").strip()[:600]
        key = re.sub(r"\d{4}-\d\d-\d\d[ T][\d:.]+(\+00:00)?", "", got)  # (the times in it move every minute)
        if got and key != GATE_SAID:
            log("lease gate: wait: %s" % got)
        GATE_SAID = key or GATE_SAID
    if code != 10:
        log("lease gate: %s; %s" % (code, "waiting (a standby starts only on a clear yes)" if standby else "starting"))
    return "wait" if code == 10 or standby else "start"


def main(argv=()):
    """argv: the command line's arguments (cron gives none); main() alone, as the tests call it, is cron's run."""
    argv = list(argv)
    if argv[:1] in (["-h"], ["--help"]):  # spec §11.1: says how and does nothing (no lock, no start, no state)
        print(__doc__)
        return 0
    if argv not in ([], ["--check-engine"]):  # anything else is a mistake, never a reason to start the bot
        print("supervisor：不認得 %s（只有 --check-engine；-h 看說明），什麼都沒做" % " ".join(argv))
        return 2
    if argv == ["--check-engine"]:  # the identity check alone, nothing started: for after a deploy
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
    crashes = []  # when the good version went down lately (restart_loop())
    held_head = None  # the held version the downs in `crashes` were of (hold())
    while True:
        if os.path.exists(os.path.join(STATE, "stop")):
            log("stop flag present; exiting")
            return 0
        if standby_here() and not token_there():  # a standby that only tells (no token: design section 2): ends
            log("standby without the bot's token: notify only, nothing to start; exiting")  # before the gate, which
            return 0  # would ask Discord with no token every 5 minutes (#code-review-2 on f47de47, manager)
        if lease_gate(crashing=bool(crashes)) == "wait":  # C-163: another machine is the bot (only with a standby:
            # else at once "start")
            time.sleep(LEASE_WAIT)
            continue
        if os.path.exists(os.path.join(STATE, "rollback-requested")):
            os.remove(os.path.join(STATE, "rollback-requested"))
            if safe_rollback(good_here(), "使用者要求退回") is False:
                notice("沒有可以退回的版本（目前就是最後一個正常版本）")
        head = git("rev-parse", "HEAD")[1]
        ver, btree = version(), bot_tree()
        good = good_here()  # (this machine's last_good, or what the other machine's bot ran when newer)
        stepped = left_behind()  # C-44: a step-back commit never becomes last_good
        held = held_for(head)  # a version too far to roll back that would not start: runs on without a trial
        if held_head and held_head != head:  # it went (!rollback, a deploy): its downs are no reason to mention the
            crashes, held_head = [], None  # owner again (restart_loop()) if the next version goes down too
        trial = (not good or version(good) != ver) and not held
        start = time.time()
        try:
            os.remove(os.path.join(STATE, "ready"))
        except OSError:
            pass
        deps_why = None
        if (deps_pending() is not None or reqs_changed()) and os.path.exists(PY):  # C-40, C-44: the packages first
            log("packages for %s pending (update.py's deps-pending, or requirements.txt not what the venv holds); "
                "installing before the start" % head[:7])
            deps_why = install_deps()
            log("packages: %s" % (deps_why or "installed"))
            if not deps_why:
                drop(FAILING)
        log("starting bot %s%s" % (head[:7], " (trial)" if trial else ""))
        env = {k: v for k, v in os.environ.items() if k != "DCBOT_CRASHING"}
        if crashes:  # C-163: its last run ended before it was up STABLE_SECS: the bot waits that long before its
            env["DCBOT_CRASHING"] = "1"  # first leader: true (bot/lease.py's note; manager 11-10)
        proc = subprocess.Popen([PY, "-m", "bot.main"], cwd=ROOT, env=env)
        write("bot.pid", str(proc.pid))
        marked, followed, next_step = False, 0, start + DEPS_RETRY
        killed, bad_n = "exit", 0  # (why the trial ended, for hold())
        while proc.poll() is None:
            time.sleep(POLL)
            up = time.time() - start
            ready = read("ready").startswith(btree)
            if trial and not marked and len(failed_events_since(start)) >= BAD_MIN:
                log("version %s set events aside as failed during its trial; killing" % head[:7])
                killed, bad_n = "events", len(failed_events_since(start))
                proc.kill()
                break
            if not marked and up > STABLE_SECS and ready:
                if head != stepped:
                    write("last_good", head)
                marked, backoff, followed = True, BACKOFF, time.time()
                log("version %s is good" % head[:7])
                crashes = []
                restart_over()  # (C-103)
                drop(HELD)
                drop("lease-down.json")  # C-163: no longer marked down in #bot-lease (tools/dclease, drill item 20)
            if marked and time.time() - followed > FOLLOW_SECS:  # commits elsewhere: last_good follows HEAD
                followed, now = time.time(), git("rev-parse", "HEAD")[1]
                if now and now != read("last_good") and now != stepped and version(now) == ver:
                    write("last_good", now)
                stepped = left_behind()
            if not trial and time.time() >= next_step and read_json(STEPPED):  # C-44: the new version's packages again
                next_step = time.time() + DEPS_RETRY
                log("stepped back: %s" % (step_forward(read_json(STEPPED), True) or "moved forward; restarting"))
            if trial and not marked and up > TRIAL_SECS and not ready:
                log("version %s never came up; killing" % head[:7])
                killed = "never_up"
                proc.kill()
        proc.wait()
        code = proc.returncode
        up = time.time() - start
        log("bot exited with %s after %ds" % (code, up))
        if code == EXIT_RESTART:
            continue  # deploy or !restart: start whatever is checked out now
        if code == EXIT_LEASE:  # C-163: the place given up; the lease decides when it starts again (lease_gate()),
            time.sleep(LEASE_WAIT)  # not at once: a loop of starts would connect to Discord again and again (#code-review)
            continue
        rec = read_json(STEPPED)
        if rec and rec.get("venv") == "mixed" and not trial and not marked and not os.path.exists(os.path.join(STATE, "stop")):
            # the old version on a venv the new version's pip left mixed: no crash backoff, the move forward at once
            why = step_forward(rec, False)
            log("old version down on a mixed venv: %s" % (why or "moved forward"))
            if why and why != "gone":
                text = ("舊版 `%s` 起不來（套件是新舊混合的），試著回到新版 `%s`：%s。每 %d 分鐘會再試；"
                        "要現在處理：在這台的引擎跑 dcbot/install.sh" % (head[:7], str(rec.get("target"))[:7], why, DEPS_RETRY // 60))
                notice("⚠️ " + text)
                tell_once("mixed-down:" + str(rec.get("target")), "⚠️ 機器人停著：" + text)
                time.sleep(DEPS_RETRY)
            continue
        if trial and not marked and deps_why:
            # pip failed before this start: its packages are not installed, no sign the version is bad, nothing rolled
            # back (C-40). Only then: a pip that worked, or none tried, and a failure is the version's own
            since = failing_since(head)
            if time.time() - since >= STEP_BACK_AFTER and step_back(good, head, deps_why):  # C-44
                continue
            text = ("新版本 `%s` 啟動不了，它要的套件還沒裝上（%s）：這不算新版壞掉，沒有退回。每 %d 分鐘會再裝一次，"
                    "%d 分鐘後還裝不上就先退回上一個能跑的版本（只在這台）；要現在處理：在這台的引擎跑 dcbot/install.sh"
                    % (head[:7], deps_why, DEPS_RETRY // 60, STEP_BACK_AFTER // 60))
            notice(text)
            tell_once(head, "⚠️ 機器人停著：" + text)
            time.sleep(DEPS_RETRY)
            continue
        if held and not marked:  # the held version down again: a line each time, no more mentions, no rollback
            held_down(head, held, code)
            crashes.append(time.time())
            held_head = head  # (for the lease gate; restart_loop() would mention the owner again)
            held_nap(backoff)
            backoff = min(backoff * 2, 300)
            continue
        size = too_far(good) if trial and not marked else None
        if size:  # too far from last_good to take back on one failed start: kept, restarted as a good version would be
            hold(head, good, size, code, killed, bad_n)
            crashes.append(time.time())
            held_head = head
            held_nap(backoff)
            backoff = min(backoff * 2, 300)
            continue
        if trial and not marked:
            if safe_rollback(good, "新版本 `%s` 啟動失敗（exit %s）" % (head[:7], code)):
                # (C-103) a new version that did not come up, rolled back on its own: the owner is told (the details
                # are in the notice). Not a rollback the owner asked for (!rollback): they are there already
                ping(said_words("rollback", "🛠️ 機器人新版有問題，已退回上一版，照常運作"))
                continue
        if not marked:  # down before it was ever up STABLE_SECS: one more in a row (C-103)
            crashes.append(time.time())
            restart_loop(crashes)
        time.sleep(backoff)  # crash of a good version: retry with backoff
        backoff = min(backoff * 2, 300)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
