"""Keeping repos in step across machines (DESIGN.md 7, stage 2.2): the engine, the instance, and this machine's
links to the instance. Standard library only, Python 3.8: altention/cron_sync.py (cron) and bin/home-pull run it.

Two separate jobs, each under its repo's own lock (`<git dir>/altention-git.lock`, flock, shared with deploy.sh and
bin/home-pull), each one never failing the other:
- engine: pull only, never pushed (C-31: a public engine's fork is public, and a commit made here can hold private
  values; who changes the engine pushes it by hand, bin/home-git push). While this machine still keeps knowledge/
  and memory/ in the engine (no instance yet), those two folders are committed here and stay here until the
  instance takes them; how many commits are not pushed is written down for doctor.
- instance: commit knowledge/ and memory/, pull, push.
Rules for both:
- commit before pulling, never autostash (git can fail to put edits back yet exit 0, leaving conflict markers);
- never rebase in the real work tree: behind only is a fast-forward; diverged is rebased in a throwaway worktree
  and the branch moved there with `reset --keep`, which keeps other channels' uncommitted edits (rebase_aside);
- a file about to be committed that has conflict markers is not committed; the error is kept for doctor;
- a rebase left halfway by a killed job is undone first (its saved edits kept in a stash entry), so a machine
  never stays stuck;
- git commands inherit the lock and have a timeout: a killed sync leaves no git running without the lock.
Links (only once this machine has an instance): Claude Code's memory folder for the engine points into the
instance (what was in the old place is merged first: same name, different content keeps both, `<name>.<host>.md`);
~/.ssh/config includes the instance's ssh_config instead of the engine's (only if the instance has one).
What happened is written to ~/.local/state/altention/sync.json for doctor. State that decides what happens lives in
~/.local/state/altention/ (STATE), never in ~/.cache, which may be cleared.
"""
import fcntl
import filecmp
import json
import os
import re
import shutil
import subprocess
import sys
import time

from . import config

LOCK_NAME = "altention-git.lock"
# The lock files git itself makes, the only ones recover() removes when stale (a killed git leaves them) and the only
# ones whose being fresh means git is still at work here (as measured with strace on git 2.43, #code-review-2
# 2026-10-16): the index, config, packed-refs, shallow, gc.pid; the top-level upper-case files (HEAD, ORIG_HEAD,
# FETCH_HEAD, REBASE_HEAD, AUTO_MERGE, MERGE_RR, MERGE_MSG, ...); the index's temporary ones (next-index-<pid>,
# index.stash.<pid>); anything under refs/, logs/ and rebase-merge/ or rebase-apply/; the same in a worktree's own
# folder (worktrees/<name>/, with config.worktree). Every other *.lock is someone's flock (ours: LOCK_NAME,
# altention-deploy.lock; an older engine's own name) and is never touched: removing one while it is held lets a
# second holder in beside the first.
GIT_OWN_LOCKS = ("index.lock", "config.lock", "packed-refs.lock", "shallow.lock", "gc.pid.lock", "config.worktree.lock")
GIT_OWN_LOCK = re.compile(r"^(?:[A-Z_]+|next-index-\d+|index\.stash\.\d+)\.lock$")
GIT_OWN_DIRS = ("refs", "logs", "rebase-merge", "rebase-apply")
OURS = "agent-rebase"  # written in the git dir while one of our jobs rebases: only such a rebase is ours to undo
FOREIGN_AGE = 1800     # a leftover rebase without our mark but with an autostash, this old, is a killed old job's
WAIT = 120
GIT_TIMEOUT = 300
STALE_INDEX_LOCK = 600
SHARED = ["knowledge", "memory"]
COUNCIL = os.path.join("skills", "council", "data")  # council's profile, lessons, changelog and runs (stage 2.4d)
BACKUP = "backup"  # the bot's channel state, copied in by `dcctl backup` (data-safety.md section 4)
INSTANCE_SHARED = SHARED + [os.path.join("docs", "refactor"), COUNCIL, "CLAUDE.global.md", "CLAUDE.md",
                            "global-notes-edited", "plugins.json", "machines.json", BACKUP]  # the
# instance's sync commits these; the two CLAUDE files are this install's halves of the global and console notes;
# the two settings files only when they read well (instance_paths), and never their removal (protected)
MARKER = re.compile(r"^(<<<<<<<|>>>>>>>) ", re.M)


def state_dir():
    """~/.local/state/altention (STATE): kept, what the sync decides by. Worked out each time: HOME may change."""
    return os.path.expanduser("~/.local/state/altention")


def status_path():
    return os.path.join(state_dir(), "sync.json")


def limited(cmd, seconds):
    """`cmd` with a time limit that holds even if the caller is killed: timeout(1), or perl's alarm where there is
    no timeout (macOS without coreutils on the PATH, as from cron or a hook)."""
    if shutil.which("timeout"):
        return ["timeout", str(seconds)] + cmd
    if shutil.which("perl"):  # in its own process group, so the time limit ends git's ssh too, as timeout(1) does
        return ["perl", "-e", PERL_LIMIT, str(seconds)] + cmd
    return cmd


PERL_LIMIT = ('my $t = shift; my $p = fork; die "fork: $!" unless defined $p;'
              'if (!$p) { setpgrp(0, 0); exec @ARGV or die "exec: $!"; }'
              '$SIG{ALRM} = sub { kill "TERM", -$p; sleep 2; kill "KILL", -$p; exit 124 };'
              'alarm $t; waitpid($p, 0); exit($? >> 8);')


class Repo(object):
    def __init__(self, path):
        self.path = path
        self.lock_fd = None

    def git(self, *args, timeout=GIT_TIMEOUT):
        cmd = limited(["git", "-C", self.path, "-c", "gc.autoDetach=false"] + list(args), timeout)  # no gc left behind
        try:
            return subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True,
                                  timeout=timeout + 10, pass_fds=(self.lock_fd,) if self.lock_fd is not None else ())
        except subprocess.TimeoutExpired:
            return subprocess.CompletedProcess(args, 124, "", "git %s：%d 秒沒有回應，已中止" % (args[0], timeout))

    def git_dir(self):
        r = self.git("rev-parse", "--git-common-dir", timeout=20)
        if r.returncode != 0:
            return None
        d = r.stdout.strip()
        return d if os.path.isabs(d) else os.path.join(self.path, d)


class Busy(Exception):
    pass


class locked(object):
    """`with locked(repo):` the repo's shared git lock (waits up to WAIT seconds, else Busy)."""

    def __init__(self, repo, wait=None):
        self.repo, self.wait, self.f = repo, WAIT if wait is None else wait, None

    def __enter__(self):
        gd = self.repo.git_dir()
        if gd is None:
            raise Busy("%s 不是 git repo" % self.repo.path)
        self.f = open(os.path.join(gd, LOCK_NAME), "a")
        until = time.time() + self.wait
        while True:
            try:
                fcntl.flock(self.f, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.time() > until:
                    self.f.close()
                    raise Busy("%d 秒內等不到 git 鎖" % self.wait)
                time.sleep(1)
        self.repo.lock_fd = self.f.fileno()
        return self.repo

    def __exit__(self, *exc):
        self.repo.lock_fd = None
        self.f.close()
        return False


def git_own_lock(rel):
    """Is `rel` (a path inside the git dir) a lock file git itself makes (GIT_OWN_LOCKS)?"""
    parts = rel.replace(os.sep, "/").split("/")
    if parts[0] == "worktrees" and len(parts) >= 3:
        parts = parts[2:]
    if not parts[-1].endswith(".lock"):
        return False
    if len(parts) == 1:
        return parts[0] in GIT_OWN_LOCKS or bool(GIT_OWN_LOCK.match(parts[0]))
    return parts[0] in GIT_OWN_DIRS


def recover(repo, say):
    """Undo what an earlier, killed job left in the repo. Call with the lock. Returns None when the repo can be
    used, else why not (left alone for now: a person resolving a rebase, or a lock still fresh)."""
    gd = repo.git_dir()
    fresh = []
    for top, dirs, names in os.walk(gd):  # index.lock, HEAD.lock, refs/heads/main.lock...: left by a killed git
        dirs[:] = [d for d in dirs if d not in ("objects", ASIDE, SAVED) and ".broken-" not in d]  # not saved copies
        for name in names:
            path = os.path.join(top, name)
            if not git_own_lock(os.path.relpath(path, gd)):
                continue
            if time.time() - os.path.getmtime(path) > STALE_INDEX_LOCK:
                os.remove(path)
                say("清掉上次留下的 %s" % os.path.relpath(path, gd))
            else:
                fresh.append(os.path.relpath(path, gd))
    aside_root = os.path.join(gd, ASIDE)  # worktrees a killed rebase_aside() left: throwaway, nothing to undo
    if os.path.isdir(aside_root):
        for name in os.listdir(aside_root):
            shutil.rmtree(os.path.join(aside_root, name), ignore_errors=True)
        repo.git("worktree", "prune", timeout=60)
    for name in ("rebase-merge", "rebase-apply"):
        left = os.path.join(gd, name)
        if not os.path.isdir(left):
            continue
        mark = os.path.join(gd, OURS)
        old_autostash = os.path.exists(os.path.join(left, "autostash")) and time.time() - os.path.getmtime(left) > FOREIGN_AGE
        if not os.path.exists(mark) and not old_autostash:
            return "有人正在這個資料夾手動 rebase（%s），這次不動它；做完後再更新" % name
        if fresh:
            return "git 還在用這個資料夾（%s 還新），下次再試" % "、".join(fresh[:3])
        # edits made since that rebase stopped (other channels kept working): saved first, abort would wipe them
        saved = save_edits(repo, gd)
        if saved:
            say("上次的 rebase 做到一半；之後在工作樹的修改已另存到 %s，請合併回來" % saved)
        if repo.git("rebase", "--abort", timeout=60).returncode == 0 and not os.path.isdir(left):
            say("中止了上次做到一半的 rebase")
            continue
        branch, rescued = "main", None
        for fname in ("head-name", "autostash"):
            try:
                with open(os.path.join(left, fname)) as f:
                    value = f.read().strip()
            except OSError:
                continue
            if fname == "head-name":
                branch = value.replace("refs/heads/", "") or "main"
            else:
                rescued = value
        kept = "%s.broken-%d" % (left, time.time())
        os.rename(left, kept)
        if rescued and repo.git("stash", "store", "-m", "edits saved by an interrupted rebase", rescued).returncode == 0:
            say("上次的 rebase 做到一半被中斷，它暫存的修改已放進 git stash（`git stash list`），請找時間合併回來")
        if repo.git("symbolic-ref", "-q", "HEAD", timeout=20).returncode != 0:
            repo.git("checkout", "-q", branch, timeout=60)
        say("上次的 rebase 殘局已移到 %s" % os.path.basename(kept))
    try:
        os.remove(os.path.join(gd, OURS))
    except OSError:
        pass
    return None


SAVED = "altention-saved"  # copies of work-tree edits taken before undoing a rebase left halfway


def save_edits(repo, gd):
    """Copy every changed or new file of the work tree (not the conflicted ones the rebase itself wrote) to
    <git dir>/altention-saved/<time>/, keeping paths. A stash cannot be made in the middle of a conflict, and
    `rebase --abort` would wipe them. Returns the folder, or None if there was nothing to save."""
    out = repo.git("status", "--porcelain", "-z", "--untracked-files=all", timeout=60).stdout.split("\0")
    files = []
    for entry in out:
        if len(entry) < 4:
            continue
        code, path = entry[:2], entry[3:]
        if "U" in code or code in ("AA", "DD"):
            continue  # the rebase's own conflict
        if os.path.isfile(os.path.join(repo.path, path)):
            files.append(path)
    if not files:
        return None
    dest = os.path.join(gd, SAVED, time.strftime("%Y%m%d-%H%M%S"))
    for path in files:
        os.makedirs(os.path.dirname(os.path.join(dest, path)), exist_ok=True)
        shutil.copy2(os.path.join(repo.path, path), os.path.join(dest, path))
    return dest


def saved_edits(repo):
    """Folders of edits saved before a rebase was undone (for doctor)."""
    root = os.path.join(repo.git_dir(), SAVED)
    return sorted(os.listdir(root)) if os.path.isdir(root) else []


def rescued_stashes(repo):
    """Stash entries holding edits an interrupted rebase had put aside (for doctor)."""
    out = repo.git("stash", "list", timeout=20).stdout
    return [line for line in out.split("\n") if "interrupted rebase" in line]


def markers_in(repo, names):
    out = []
    for name in names:
        try:
            with open(os.path.join(repo.path, name), encoding="utf-8", errors="replace") as f:
                if MARKER.search(f.read()):
                    out.append(name)
        except OSError:
            continue
    return out


def commit(repo, paths, message, protected=()):
    """Commit what changed under `paths`. (ok, note). Files with conflict markers are left out, and said.
    A path that is gone while git still has it: its removal is committed (a folder removed as a whole), except for
    the `protected` ones (folders in use: memory, knowledge, what the instance holds), whose disappearance is far
    more likely an accident than a wish; that is reported and nothing of it committed, so it stays on this one
    machine instead of reaching every other within a round."""
    gone_protected = []
    keep = []
    for p in paths:
        if os.path.exists(os.path.join(repo.path, p)):
            keep.append(p)
        elif repo.git("ls-files", "--", p, timeout=60).stdout.strip():
            (gone_protected if p in protected else keep).append(p)
    paths = keep
    if gone_protected and not paths:
        return False, gone_message(repo, gone_protected)
    if not paths:
        return True, None
    for p in paths:  # one at a time: an empty folder git knows nothing of must not stop the others
        repo.git("add", "-A", "--", p, timeout=60)
    # -z: names as they are (non-ASCII ones are not quoted); --no-renames: a rename is its delete and its add, both
    staged = [s for s in repo.git("diff", "--cached", "--name-only", "-z", "--no-renames", "--", *paths,
                                  timeout=60).stdout.split("\0") if s]
    bad = markers_in(repo, staged)
    files = [s for s in staged if s not in bad]
    if bad:
        repo.git("reset", "-q", "--", *bad, timeout=60)
    held_back = mass_deletes(repo, files, protected)  # many files of a folder in use at once: someone confirms first
    if held_back:
        dropped = [f for _, dels, _ in held_back for f in dels]
        repo.git("reset", "-q", "--", *dropped, timeout=60)
        files = [f for f in files if f not in dropped]
    if files:  # the files, not the folders: `git commit -- folder` takes the work tree's content, markers and all
        r = repo.git("commit", "-q", "-m", message, "--", *files, timeout=60)
        if r.returncode != 0:
            return False, "commit 失敗：%s" % (r.stderr.strip() or r.stdout.strip())[:300]
    if bad:
        return False, "有衝突標記，沒有提交：%s" % "、".join(bad)
    if gone_protected:
        return False, gone_message(repo, gone_protected)
    if held_back:
        return False, "；".join("一次要刪掉 %s 裡 %d 個檔（共 %d 個），超過門檻，沒有提交刪除；確認是要刪的話跑 "
                               "`cd %s && python3 -m altention.sync --allow-delete %s %s`，下一輪同步就會提交" % (
                                   p, len(dels), total, config.ENGINE, repo.path, p)
                               for p, dels, total in held_back)
    return True, None


def gone_message(repo, folders):
    return ("%s 整個不見了（git 裡還有），沒有提交刪除。是意外的話在 %s 跑 `git checkout -- %s` 救回來；"
            "真的要從實例拿掉，走 `python3 ops/instance.py move-undo`" % ("、".join(folders), repo.path, " ".join(folders)))


def delete_limit(total):
    """How many files of a folder in use one commit may delete: fewer than half and at most 10 by default
    (plugins.json sync.mass_delete: {"fraction": 0.5, "max": 10})."""
    try:
        conf = config.setting("sync.mass_delete") or {}
    except Exception:
        conf = {}
    fraction, most = float(conf.get("fraction", 0.5)), int(conf.get("max", 10))
    return min(max(1, int(total * fraction)), most)


def allow_path():
    return os.path.join(state_dir(), "allow-delete.json")


def mass_deletes(repo, files, protected):
    """[(folder, deleted files, files it had)] for each protected folder whose staged deletions in `files` go over
    delete_limit() and that nobody allowed (allow_delete). An allowance is used up here."""
    out = []
    if not protected or not files:
        return out
    gone = set(f for f in repo.git("diff", "--cached", "--name-only", "-z", "--no-renames", "--diff-filter=D",
                                   timeout=60).stdout.split("\0") if f)
    allowed = config.read_json(allow_path()) or {}
    used = False
    for p in protected:
        dels = [f for f in files if f in gone and f.startswith(p.rstrip("/") + "/")]
        if not dels:
            continue
        total = len([f for f in repo.git("ls-tree", "-r", "--name-only", "HEAD", "--", p, timeout=60).stdout.splitlines() if f])
        key = "%s:%s" % (os.path.realpath(repo.path), p)
        if len(dels) <= delete_limit(total):
            continue
        if allowed.get(key, 0) > time.time():
            allowed.pop(key)
            used = True
            continue
        out.append((p, dels, total))
    if used:
        os.makedirs(state_dir(), exist_ok=True)
        with open(allow_path() + ".tmp", "w") as f:
            json.dump(allowed, f)
        os.replace(allow_path() + ".tmp", allow_path())
    return out


def allow_delete(repo_path, folder, hours=24):
    """Let the next sync commit many deletions in `folder` of the repo at `repo_path` (once, within `hours`)."""
    allowed = config.read_json(allow_path()) or {}
    allowed["%s:%s" % (os.path.realpath(os.path.expanduser(repo_path)), folder.rstrip("/"))] = time.time() + hours * 3600
    os.makedirs(state_dir(), exist_ok=True)
    with open(allow_path() + ".tmp", "w") as f:
        json.dump(allowed, f)
    os.replace(allow_path() + ".tmp", allow_path())


def update(repo, to=None):
    """Bring the branch up to its upstream without autostash. (code, note): 0 fine, 1 refused or failed.
    to: up to that commit instead, not fetched again (update_instance: what is taken is what was checked)."""
    if repo.git("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}", timeout=20).returncode != 0:
        if not repo.git("remote", timeout=20).stdout.strip():
            return 0, "沒有設定遠端，不用更新"
        if repo.git("symbolic-ref", "-q", "HEAD", timeout=20).returncode != 0:
            return 1, "HEAD 不在任何分支上（detached），沒有更新；請 checkout 回 main"
        return 1, "這個分支沒有設定追蹤的遠端分支，沒有更新（git branch -u origin/main）"
    r = repo.git("fetch", "-q") if to is None else None
    if r is not None and r.returncode != 0:
        return 1, "fetch 失敗：%s" % (r.stderr.strip() or r.stdout.strip())[:300]
    up = to or "@{u}"
    counts = repo.git("rev-list", "--left-right", "--count", "HEAD...%s" % up, timeout=20).stdout.split()
    ahead, behind = (int(counts[0]), int(counts[1])) if len(counts) == 2 else (0, 0)
    said = None
    if behind:
        if ahead == 0:
            r = repo.git("merge", "-q", "--ff-only", up)
            if r.returncode != 0:
                return 1, "沒有更新：沒提交的修改跟遠端改到同一個檔（%s）" % (r.stderr.strip() or r.stdout.strip())[:300]
        elif to is not None:  # a release (C-07): a rollback the supervisor made here is never put on top of a new one
            backs, others = local_commits(repo, to)
            if backs is None:
                return 1, "沒有更新：讀不出這台在發佈版本之上有哪些 commit（%s），不猜" % others
            if backs and not others:
                r = repo.git("reset", "-q", "--keep", to, timeout=120)
                if r.returncode != 0:
                    return 1, "沒有更新：沒提交的修改跟要合進來的變更改到同一個檔（%s）" % (r.stderr.strip() or r.stdout.strip())[:300]
                said = "這台的退回 commit（%s）沒有帶到新版上：它只是避開舊版的問題，新版取代它" % "、".join(h[:7] for h in backs)
            elif backs or any(looks for _, looks in others):
                return 1, ("沒有更新：這台在發佈版本之上有退回 commit，也有別的 commit（%s），不猜哪些要留；"
                           "請人看 git log 之後處理（只留自己的 commit，退回的不要帶到新版上）"
                           % "、".join(h[:7] + ("（退回）" if looks else "") for h, looks in
                                      ([(h, True) for h in backs] + others)[:8]))
        if said is None and ahead:
            code, note = rebase_aside(repo, to)
            if code:
                return code, note
    if repo.git("ls-files", "-u", timeout=20).stdout.strip():
        return 1, "有沒解決的衝突，請人工處理"
    return 0, said


# the subject dcbot/supervisor.py put_back() gives a rollback commit
ROLLBACK_SUBJECT = re.compile(r"^dcbot: roll back( \(partial\))? to ([0-9a-f]{7}) \(.*\)$")


def local_commits(repo, up):
    """(rollbacks, others): the commits HEAD has and `up` has not. rollbacks: those the supervisor made here, only
    when all of it says so (manager 2026-10-17): the subject put_back gives it, one parent, this repo's own author
    (git var GIT_AUTHOR_IDENT), on no remote branch, and what it does is only that: every file it changes is, in it,
    as in the good commit its subject names (gone there: not in good either; #code-review of 239abcc: the author
    is this repo's for the owner's commits too, and an amended rollback or a commit of the owner's with that subject
    carried a file of theirs away). others: [(hash, looks like a rollback)] for the rest, a commit with that subject
    that fails another of these marked (the caller stops rather than put it on a release). (None, why) when git
    cannot list them."""
    ident = repo.git("var", "GIT_AUTHOR_IDENT", timeout=20)
    here = repo.git("rev-list", "HEAD", "--not", up, "--remotes", timeout=20)  # on no remote branch
    log = repo.git("log", "--format=%H%x00%P%x00%ae%x00%s", "%s..HEAD" % up, timeout=20)
    for r in (ident, here, log):
        if r.returncode != 0:
            return None, (r.stderr.strip() or r.stdout.strip())[:200]
    me = ident.stdout[ident.stdout.find("<") + 1:ident.stdout.find(">")]
    here = set(here.stdout.split())
    backs, others = [], []
    for line in log.stdout.splitlines():
        h, parents, author, subject = (line.split("\0") + ["", "", ""])[:4]
        m = ROLLBACK_SUBJECT.match(subject)
        if m and me and author == me and h in here and len(parents.split()) == 1 and restores(repo, h, m.group(2)):
            backs.append(h)
        else:
            others.append((h, bool(m)))
    return backs, others


def restores(repo, h, good):
    """Does commit `h` only put files back as they are in `good` (a short hash)? Every path it changes (against its
    parent) is in `h` what it is in `good`, a path it removes is not in `good`. False when `good` is not one commit
    git knows, or git cannot tell."""
    full = repo.git("rev-parse", "-q", "--verify", "%s^{commit}" % good, timeout=20)
    changed = repo.git("diff-tree", "--no-commit-id", "-r", "-z", "--name-only", "--no-renames", "%s^" % h, h, timeout=60)
    if full.returncode != 0 or changed.returncode != 0:
        return False
    paths = [p for p in changed.stdout.split("\0") if p]
    for i in range(0, len(paths), 200):  # identical there: git diff --quiet says 0
        r = repo.git("--literal-pathspecs", "diff", "--quiet", "--no-renames", full.stdout.strip(), h, "--",
                     *paths[i:i + 200], timeout=60)
        if r.returncode != 0:
            return False
    return bool(paths)


ASIDE = "altention-rebase"  # temporary worktrees for rebasing, under the git dir


def rebase_aside(repo, to=None):
    """Put this machine's unpushed commits on top of the upstream without touching the work tree: rebase in a
    temporary worktree, then move the branch there with `reset --keep`, which keeps uncommitted edits (other
    channels' work in progress) and refuses rather than lose one. So a dirty tree no longer stops the sync, no
    autostash is needed, and a rebase killed halfway is in the throwaway worktree, never in the real one."""
    gd = repo.git_dir()
    aside = os.path.join(gd, ASIDE, "%d-%d" % (os.getpid(), time.time()))
    os.makedirs(os.path.dirname(aside), exist_ok=True)
    start = repo.git("rev-parse", "HEAD", timeout=20).stdout.strip()
    r = repo.git("worktree", "add", "-q", "--detach", aside, start, timeout=120)
    if r.returncode != 0:
        return 1, "建暫時的工作目錄失敗：%s" % (r.stderr.strip() or r.stdout.strip())[:300]
    try:
        upstream = to or repo.git("rev-parse", "@{u}", timeout=20).stdout.strip()  # the detached worktree has no @{u}
        r = subprocess.run(limited(["git", "-C", aside, "-c", "gc.autoDetach=false", "rebase", "-q", upstream], GIT_TIMEOUT),
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, timeout=GIT_TIMEOUT + 10,
                           pass_fds=(repo.lock_fd,) if repo.lock_fd is not None else ())
        if r.returncode != 0:
            subprocess.run(["git", "-C", aside, "rebase", "--abort"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return 1, "rebase 失敗（兩台改到同一段），這台維持原樣，需要人合併：%s" % (r.stderr.strip() or r.stdout.strip())[:300]
        new = subprocess.run(["git", "-C", aside, "rev-parse", "HEAD"], stdout=subprocess.PIPE,
                             universal_newlines=True).stdout.strip()
        if repo.git("rev-parse", "HEAD", timeout=20).stdout.strip() != start:  # someone committed meanwhile (no lock)
            return 1, "同步期間這裡有新的 commit，這次不移動分支，下次再試"
        r = repo.git("reset", "-q", "--keep", new, timeout=120)
        if r.returncode != 0:
            return 1, "沒有更新：沒提交的修改跟要合進來的變更改到同一個檔（%s）" % (r.stderr.strip() or r.stdout.strip())[:300]
        return 0, None
    finally:
        repo.git("worktree", "remove", "--force", aside, timeout=120)
        shutil.rmtree(aside, ignore_errors=True)


def push(repo, track=False):
    """track: a data repo given its origin but no upstream yet (ops/remote.py attach, or an empty backup repo):
    its branch is pushed there and tracked."""
    if repo.git("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}", timeout=20).returncode != 0:
        if track and repo.git("remote", "get-url", "origin", timeout=20).returncode == 0:
            branch = repo.git("symbolic-ref", "-q", "--short", "HEAD", timeout=20).stdout.strip()
            if branch and repo.git("rev-parse", "-q", "--verify", "HEAD", timeout=20).returncode == 0:
                r = repo.git("push", "-q", "-u", "origin", branch)
                return (r.returncode == 0), (None if r.returncode == 0 else "push 失敗：%s" % (r.stderr.strip() or r.stdout.strip())[:300])
        return True, None
    if repo.git("rev-list", "--count", "@{u}..HEAD", timeout=20).stdout.strip() in ("", "0"):
        return True, None
    r = repo.git("push", "-q")
    return (r.returncode == 0), (None if r.returncode == 0 else "push 失敗：%s" % (r.stderr.strip() or r.stdout.strip())[:300])


def has_upstream(repo):
    return repo.git("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}", timeout=20).returncode == 0


def track_upstream(repo):
    """A data repo with an origin but no upstream: its branch made to track origin's branch of the same name when
    the remote has one (fetched first); an empty remote is left for push(track=True). None, or what went wrong."""
    if repo.git("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}", timeout=20).returncode == 0:
        return None
    branch = repo.git("symbolic-ref", "-q", "--short", "HEAD", timeout=20).stdout.strip()
    if not branch:
        return "HEAD 不在任何分支上（detached），沒有同步；請 checkout 回 main"
    r = repo.git("fetch", "-q", "origin")
    if r.returncode != 0:
        return "fetch 失敗：%s" % (r.stderr.strip() or r.stdout.strip())[:300]
    if repo.git("rev-parse", "-q", "--verify", "refs/remotes/origin/%s" % branch, timeout=20).returncode == 0:
        r = repo.git("branch", "-q", "-u", "origin/%s" % branch, timeout=20)
        if r.returncode != 0:
            return "設不了追蹤的遠端分支：%s" % (r.stderr.strip() or r.stdout.strip())[:300]
    return None


def commits_ahead(repo):
    """How many commits HEAD has that its upstream does not; 0 without an upstream or when git cannot tell."""
    r = repo.git("rev-list", "--count", "@{u}..HEAD", timeout=20)
    n = r.stdout.strip()
    return int(n) if r.returncode == 0 and n.isdigit() else 0


def sync_repo(path, shared, message, say, before=None, after=None, protected=(), pull=True, refuse="", instance=False,
              push_out=True, track=False, guard=None):
    """One repo, under its lock: recover, commit `shared`, update, push. {"ok", "error", "at"}. before(repo), if
    given, runs first under the lock and returns the list to commit instead of `shared`; after(repo) runs after the
    update, before the push. pull=False (the engine while altention/update.py is alive, 3a.10): no update here, and
    no push while behind (update.py rebases the local commits first; the next round pushes them). push_out=False
    (the engine, C-31): never pushed; "ahead" says how many commits here are not on the upstream. instance=True:
    updated by update_instance (its settings checked first); an upstream held back is not an error of the sync:
    "held" says why, this machine's commits stay here unpushed, and a line in the alerts file says how many.
    track=True (a plugin's data repo, C-26): an origin without an upstream is tracked or pushed to (track_upstream,
    push). guard(repo), if given, runs before the push: what it returns (why not) stops the push, said."""
    repo = Repo(path)
    result = {"at": time.time(), "ok": False, "error": None}
    try:
        with locked(repo):
            why = recover(repo, say)
            if why:
                result["error"], result["stuck"] = why, True
                return result
            notes = []
            if before:
                shared = before(repo)
            if shared:
                ok, note = commit(repo, shared, message, protected)
                if note:
                    notes.append(note)
            behind = False
            if pull and refuse:  # update.py refused this hash: fetched first, so a fix pushed since is seen
                fetched = repo.git("fetch", "-q")
                if fetched.returncode != 0:  # not known this round: not pulled, said (manager 2026-10-06)
                    pull, result["refused"] = False, refuse
                    _alert_plain("sync", "fetch", "代為更新前 fetch 失敗（%s），這一輪不知道上游是不是被擋的那個，沒有拉"
                                 % (fetched.stderr.strip() or fetched.stdout.strip())[:160])
                elif repo.git("rev-parse", "@{u}", timeout=20).stdout.strip() == refuse:
                    pull, result["refused"] = False, refuse
                elif stacked_on(repo, refuse):  # update.py is not checking: pulled (a fix must get through), never quietly
                    up = repo.git("rev-parse", "--short=12", "@{u}", timeout=20).stdout.strip()
                    _alert_plain("sync", "stacked-on-refused", "update 沒在檢查，拉進了疊在被擋的 %s 上面的 %s，沒有檢查"
                                 % (refuse[:12], up))
            if pull and instance:
                code, note, held = update_instance(repo)
                if code != 0:
                    notes.append(note)
                if held:  # not rebased onto it, so not pushable: kept here until the upstream is fixed or taken
                    behind, result["held"] = True, held
            elif pull:
                untracked = track_upstream(repo) if track else None
                if untracked:
                    code, note = 1, untracked
                elif track and not has_upstream(repo):  # an empty remote: nothing to take, pushed to below
                    code, note = 0, None
                else:
                    code, note = update(repo)
                if code != 0:
                    notes.append(note)
                behind = bool(untracked)  # the remote not read this round: nothing pushed either
            else:
                behind = repo.git("rev-list", "--count", "HEAD..@{u}", timeout=20).stdout.strip() not in ("", "0")
            if after:
                after(repo)
            if not push_out:
                result["ahead"] = commits_ahead(repo)
            elif not behind:
                stop = guard(repo) if guard else None
                ok, note = (False, stop) if stop else push(repo, track)
                if note and not pull and any(w in note for w in ("rejected", "non-fast-forward", "fetch first")):
                    result["waiting"] = "push 被拒（遠端剛又動了），等 update.py 下一輪 rebase 之後再推"
                elif note:
                    notes.append(note)
            result["ok"] = not notes
            result["error"] = "；".join(notes) or None
            result["rescued"] = rescued_stashes(repo) + ["%s/%s" % (SAVED, d) for d in saved_edits(repo)]
    except Busy as e:
        result["error"] = str(e)
    except Exception as e:  # one repo's trouble never stops the other job
        result["error"] = "%s: %s" % (type(e).__name__, e)
    return result


def slug(path):
    return re.sub(r"[^A-Za-z0-9]", "-", path)


def memory_links():
    """Claude Code's memory folders for the engine's path (its real one)."""
    home = os.path.expanduser("~")
    paths = [os.path.realpath(config.ENGINE)]
    return [os.path.join(home, ".claude", "projects", slug(p), "memory") for p in paths]


def merge_into(src, dest, host, say):
    """Copy files of src that dest lacks; same name with other content is kept as <name>.<host><ext>. Returns
    the names kept twice (for doctor)."""
    twice = []
    if not os.path.isdir(src) or os.path.realpath(src) == os.path.realpath(dest):
        return twice
    for name in sorted(os.listdir(src)):
        s, d = os.path.join(src, name), os.path.join(dest, name)
        if os.path.isdir(s) and not os.path.islink(s):  # subfolders too
            os.makedirs(d, exist_ok=True)
            twice += merge_into(s, d, host, say)
            continue
        if not os.path.isfile(s):
            continue
        if not os.path.exists(d):
            shutil.copy2(s, d)
        elif not filecmp.cmp(s, d, shallow=False):
            base, ext = os.path.splitext(name)
            other = os.path.join(dest, "%s.%s%s" % (base, host, ext))
            if not os.path.exists(other) or not filecmp.cmp(s, other, shallow=False):
                shutil.copy2(s, other)
                twice.append(os.path.basename(other))
                say("%s 兩邊內容不同，這台的版本另存成 %s" % (name, os.path.basename(other)))
    return twice


MOVED = ["knowledge", "memory", os.path.join("docs", "refactor"), COUNCIL]  # the engine's folders the instance may hold


def copy_in(files, src_root, inst_dir, name, host, say):
    """Copy the engine's `files` (paths relative to the engine, under `name`) into the instance's folder: a file
    the instance lacks is copied; same name, other content: kept as <name>.<host><ext>. Returns those kept twice."""
    twice = []
    for rel in files:
        src = os.path.join(src_root, rel)
        if not os.path.isfile(src):
            continue
        dest = os.path.join(inst_dir, os.path.relpath(rel, name))
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        if not os.path.exists(dest):
            shutil.copy2(src, dest)
        elif not filecmp.cmp(src, dest, shallow=False):
            base, ext = os.path.splitext(dest)
            other = "%s.%s%s" % (base, host, ext)
            if not os.path.exists(other) or not filecmp.cmp(src, other, shallow=False):
                shutil.copy2(src, other)
                twice.append(os.path.join(name, os.path.relpath(other, inst_dir)))
                say("%s 兩邊內容不同，這台的版本另存成 %s" % (rel, twice[-1]))
    return twice


class Unknown(Exception):
    """The instance's plugins.json cannot be read: who holds what is unknown, so nothing is moved."""


def owns_list(instance):
    """The engine paths the instance holds: plugins.json's sync.owns, a tracked list (stage 2.3b: prepare-2.3b adds
    knowledge and docs/refactor, undo-2.3b takes them out). Memory is the instance's whenever it has the folder
    (stage 2.3a, before there was a list). Raises Unknown when plugins.json exists and cannot be read."""
    path = os.path.join(instance, "plugins.json")
    if not os.path.exists(path):
        return []
    data = config.read_json(path)
    if not isinstance(data, dict):
        raise Unknown("讀不了 %s，這輪不搬任何東西" % path)
    return list(((data.get("sync") or {}).get("owns")) or [])


def owned_by_instance(instance, name):
    """The switch: the instance holds `name` (listed, and the folder there)."""
    if not instance or not os.path.isdir(os.path.join(instance, name)):
        return False
    return name == "memory" or name in owns_list(instance)


def set_owns(instance, names, on):
    """Add `names` to plugins.json's sync.owns (on) or take them out; the caller commits it. Keeps everything else."""
    path = os.path.join(instance, "plugins.json")
    data = config.read_json(path) if os.path.exists(path) else {"format": 1}
    if not isinstance(data, dict):
        raise Unknown("讀不了 %s" % path)
    owns = [n for n in ((data.setdefault("sync", {})).get("owns") or []) if on or n not in names]
    owns += [n for n in names if on and n not in owns]
    data["sync"]["owns"] = owns
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    os.replace(tmp, path)


def aside_path(name):
    """Where a folder set aside goes: STATE/before-instance/<name>-<time>, made ready."""
    aside = os.path.join(os.path.dirname(status_path()), "before-instance", "%s-%d" % (name.replace(os.sep, "-"), time.time()))
    os.makedirs(os.path.dirname(aside), exist_ok=True)
    return aside


HANDBACK_AFTER = 300  # seconds: the same state seen again this much later (a later round) before handing back


def seen_twice(instance, name):
    """True when a folder the list does not name was already seen at the same instance commit a round ago
    (HANDBACK_AFTER seconds or more). The first sighting is only written down (STATE/handback.json):
    handing back affects every machine and is only needed after undo-2.3b, so a round later costs nothing."""
    path = os.path.join(os.path.dirname(status_path()), "handback.json")
    seen = config.read_json(path) or {}
    head = Repo(instance).git("rev-parse", "HEAD", timeout=20).stdout.strip()
    before = seen.get(name) or {}
    if before.get("head") == head and time.time() - before.get("at", time.time()) >= HANDBACK_AFTER:
        seen.pop(name, None)
        ok = True
    else:
        if before.get("head") != head:
            seen[name] = {"head": head, "at": time.time()}
        ok = False
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path + ".tmp", "w") as f:
        json.dump(seen, f)
    os.replace(path + ".tmp", path)
    return ok


def adopted_refs(repo):
    """Unpushed commits adopt() took back (their content went to the instance), kept as refs for doctor."""
    r = repo.git("for-each-ref", "--format=%(refname)", "refs/altention/adopted/", timeout=20)
    return [l for l in r.stdout.splitlines() if l] if r.returncode == 0 else []


def unpushed_files(engine):
    """Files the commits not yet pushed change, and whether those commits touch nothing outside MOVED. ([], True)
    when there is no upstream or nothing unpushed."""
    if engine.git("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}", timeout=20).returncode != 0:
        return [], False
    r = engine.git("diff", "--name-only", "-z", "--no-renames", "@{u}...HEAD", timeout=60)
    if r.returncode != 0:
        return [], False
    files = [f for f in r.stdout.split("\0") if f]
    only = all(any(f == m or f.startswith(m + "/") for m in MOVED) for f in files)
    return files, only


# COMPAT: 知識等資料夾還在引擎裡的機器（2.3b 的搬移）、和還寫 ~/agent-home/knowledge 的說明；每台的引擎裡都沒有這些資料夾的實體、說明不再用 ~/agent-home/<資料夾> 之後
def adopt(instance, name, host, say):
    """The engine's `name`, by the switch (owned_by_instance). Call with the engine's lock held. (state, twice):
    - the instance does not hold it: "engine": the engine's folder is the one in use and synced in the engine; a
      link of ours left there (undo-2.3b removed the instance's folder) is taken away, for git to put it back;
    - it does, the engine's path is a link: "link";
    - it does, the engine's path missing: a link is made: "link";
    - it does, a real folder: everything in it the instance may not have goes there (same name, other content:
      kept twice): edits not committed (then put back as committed), commits not pushed yet (taken back when they
      touch nothing else), and, once the engine's .gitignore gives the path up (stage 2.3b), files git still
      tracks there (a machine that committed them late): their removal is committed here and pushed by the sync.
      Then, git tracking nothing there, the folder is renamed aside (never deleted) and a link made: "link".
      While git still tracks it and the engine has not given it up: "tracked"."""
    inst_dir, eng_dir = os.path.join(instance, name), os.path.join(config.ENGINE, name)
    if not owned_by_instance(instance, name):
        twice = []
        if os.path.islink(eng_dir) and os.readlink(eng_dir) == inst_dir:
            os.remove(eng_dir)
            say("實例不再管 %s，拿掉引擎裡 %s 的連結" % (name, name))
        if os.path.isdir(inst_dir) and name != "memory" and os.path.isdir(eng_dir) and not os.path.islink(eng_dir):
            # the folder is there but the list does not name it (a machine pushed a file into it after undo-2.3b):
            # its files go back to the engine, and the folder is set aside (never deleted); the instance's sync
            # commits its removal
            # only with the instance's lock, never waiting: its sync, a pull and prepare-2.3b hold it throughout, so a
            # folder written just before its list (a checkout halfway, prepare halfway) is never taken for this
            try:
                with locked(Repo(instance), wait=0):
                    if os.path.isdir(inst_dir) and not owned_by_instance(instance, name) and \
                            seen_twice(instance, name):  # still so, under the lock; and a round ago too
                        files = []
                        for top, _, names in os.walk(inst_dir):
                            files += [os.path.relpath(os.path.join(top, n), instance) for n in names]
                        twice += copy_in(files, instance, eng_dir, name, host, say)
                        shutil.move(inst_dir, aside_path("handed-back-" + name))
                        say("實例沒有列 %s，裡面的檔搬回引擎" % name)
            except Busy:
                pass  # the instance is busy: next time
        return "engine", twice
    if os.path.islink(eng_dir):
        return "link", []
    twice = []
    if os.path.isdir(eng_dir):
        engine = Repo(config.ENGINE)
        st = engine.git("status", "--porcelain", "-z", "--untracked-files=all", "--no-renames", "--", name, timeout=60)
        if st.returncode != 0:  # not a git repo, or git failed: touch nothing
            return "tracked", []
        changed = [e[3:] for e in st.stdout.split("\0") if len(e) > 3]
        twice += copy_in(changed, config.ENGINE, inst_dir, name, host, say)
        if changed:
            engine.git("checkout", "-q", "HEAD", "--", name, timeout=60)  # back as committed, staged edits too
            engine.git("clean", "-q", "-f", "--", name, timeout=60)  # files git never knew: copied above
            say("引擎 %s 裡沒提交的修改已併進實例" % name)
        files, _ = unpushed_files(engine)
        if any(f.startswith(name + "/") for f in files):
            held = [m for m in MOVED if owned_by_instance(instance, m)]
            where = dict((f, next((m for m in held if f.startswith(m + "/")), None)) for f in files)
            for m in held:  # every held path these commits touch, not only this one
                mine = [f for f in files if where[f] == m]
                if mine:
                    twice += copy_in(mine, config.ENGINE, os.path.join(instance, m), m, host, say)
            if all(where.values()):  # nothing outside what the instance holds: the commits can go, kept as a ref
                head = engine.git("rev-parse", "HEAD", timeout=20).stdout.strip()
                ref = "refs/altention/adopted/%d" % time.time()
                if head and engine.git("update-ref", ref, head, timeout=20).returncode == 0 and \
                        engine.git("reset", "-q", "--keep", "@{u}", timeout=120).returncode == 0:
                    say("引擎裡還沒推的修改（%s）已併進實例，那些 commit 拿掉了，留在 %s" %
                        ("、".join(sorted(set(where.values()))), ref))
        if engine.git("check-ignore", "-q", "--no-index", "--", name, timeout=20).returncode == 0:
            tracked = [f for f in engine.git("ls-files", "-z", "--", name, timeout=60).stdout.split("\0") if f]
            if tracked:  # committed late, after the engine gave the path up: hand them over
                twice += copy_in(tracked, config.ENGINE, inst_dir, name, host, say)
                staged = [f for f in engine.git("diff", "--cached", "--name-only", "-z", timeout=60).stdout.split("\0") if f]
                if staged:  # someone's staged work: never commit it along; next time
                    return "tracked", twice
                engine.git("rm", "-r", "-q", "--cached", "--", name, timeout=60)
                # not `commit -- name`: with a pathspec git takes the work tree's state, and these files are ignored
                r = engine.git("commit", "-q", "-m", "sync: %s handed over to the instance @ %s" % (name, host), timeout=60)
                if r.returncode != 0:
                    engine.git("reset", "-q", "--", name, timeout=60)
                    return "tracked", twice
                say("引擎裡晚到的 %s 檔已併進實例，引擎那邊拿掉" % name)
        r = engine.git("ls-files", "--", name, timeout=60)
        if r.returncode != 0 or r.stdout.strip():  # still git's (or git could not say): left as it is
            return "tracked", twice
        if os.path.isdir(eng_dir):  # (git clean may have removed it, emptied)
            twice += merge_into(eng_dir, inst_dir, host, say)  # whatever is left in it goes to the instance first
            shutil.move(eng_dir, aside_path(name))  # kept, never deleted; out of the engine's tree (no stray `??`)
    os.makedirs(os.path.dirname(eng_dir), exist_ok=True)
    os.symlink(inst_dir, eng_dir)
    say("引擎裡的 %s 改成指向實例的連結" % name)
    return "link", twice


def _host():
    """This machine's name for naming kept copies (<file>.<host>.md): the list's when it can be read, else the name
    it states or the hostname. Never raises: a broken machine list must not stop a sync step that only names files."""
    try:
        return config.this_machine()
    except Exception:
        try:
            return config.stated_name() or config.hostname()
        except Exception:
            return "unknown-host"


def is_checkout(engine=None):
    """Is the engine a git checkout? An engine unpacked from an archive has no .git: nothing of it is synced or
    pulled (altention/update.py says the same)."""
    return os.path.lexists(os.path.join(os.path.realpath(engine or config.ENGINE), ".git"))


def adopt_engine_paths(instance, say):
    """adopt_all() under the engine's lock; an engine unpacked from an archive has no git and no lock to take
    (install.sh, so the links stand before the first sync; run() for such an engine)."""
    if not is_checkout():
        return adopt_all(instance, say)
    with locked(Repo(config.ENGINE)):
        return adopt_all(instance, say)


def adopt_all(instance, say):
    """adopt() for every path in MOVED. Call with the engine's lock held. ({name: state}, twice)."""
    host, states, twice = _host(), {}, []
    for name in MOVED:
        try:
            owns_list(instance)  # unreadable: Unknown, and nothing is moved this time
            states[name], t = adopt(instance, name, host, say)
            twice += t
        except Exception as e:  # one path's trouble never stops the others
            states[name] = "error: %s: %s" % (type(e).__name__, e)
    return states, twice


GLOBAL_MARK = ("<!-- altention：這份由引擎的 CLAUDE.global.md 加上實例的 CLAUDE.global.md 組成，同步會覆寫；"
               "要改請改那兩份 -->")


EDITED = "global-notes-edited"  # in the instance: copies of ~/.claude/CLAUDE.md someone edited in ways not merged


def keep_copy(instance, current, say):
    """A copy of ~/.claude/CLAUDE.md in the instance's global-notes-edited/ (one per content). True if new."""
    import hashlib
    folder = os.path.join(instance, EDITED)
    kept = os.path.join(folder, "%s-%s.md" % (config.this_machine() or "machine",
                                              hashlib.sha1(current.encode("utf-8")).hexdigest()[:12]))
    if os.path.exists(kept):
        return False
    os.makedirs(folder, exist_ok=True)
    with open(kept, "w", encoding="utf-8") as f:
        f.write(current)
    say("~/.claude/CLAUDE.md 的原內容另存在實例的 %s/" % EDITED)
    return True


def engine_for_claude():
    """This machine's engine as the global notes name it ({engine} there): the full path, ~ for this home, quoted for
    a shell only when it has to be. Claude is told full paths, never a bare `altention` that needs ~/.local/bin on
    PATH (a Bash tool that is not a login shell may not have it: manager 2026-10-14)."""
    engine, home = os.path.realpath(config.ENGINE), os.path.expanduser("~")
    engine = "~" + engine[len(home):] if engine == home or engine.startswith(home + "/") else engine
    return config.shell_path(engine) if re.search(r"[^\w/~.+-]", engine) else engine


def compose_global(instance, say):
    """Under a lock of its own (two syncs at once must not take each other's write for an edit); see _compose."""
    path = os.path.join(state_dir(), "global.lock")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        return _compose(instance, say)


def _compose(instance, say):
    """~/.claude/CLAUDE.md, every Claude's global notes: the engine's CLAUDE.global.md (for every install) followed by
    the instance's (this user's), written as one file. Replaces only what is ours: a missing file, a link into the
    engine or the instance, or a file this wrote before (its first line). A file of the user's own is left: "own".

    The file may be written to in between (Claude Code's "remember this" for the user level writes here). What
    this wrote last is kept in STATE/global.last (not ~/.cache: clearing that must not change what happens);
    when the file is not what would be written now and differs from that record, someone edited it:
    - lines only added at the end: they go to the end of the instance's CLAUDE.global.md (so every machine gets
      them, as when the file was a link into the engine), then it is composed again: "merged";
    - anything else (lines changed or removed): the file is copied into the instance's global-notes-edited/ and
      left as it is, not overwritten, until someone looks (doctor says so): "edited";
    - no record at all (cleared, or never written by this): copied into global-notes-edited/ too, but then
      overwritten, so this machine keeps getting updates.
    Nothing written there is ever lost."""
    target = os.path.expanduser("~/.claude/CLAUDE.md")
    last_path = os.path.join(state_dir(), "global.last")
    eng, mine = os.path.join(config.ENGINE, "CLAUDE.global.md"), os.path.join(instance, "CLAUDE.global.md")

    def composed():
        parts = []
        for path in (eng, mine):
            if os.path.isfile(path):
                with open(path, encoding="utf-8") as f:
                    parts.append(f.read().strip("\n").replace("{engine}", engine_for_claude()))
        return GLOBAL_MARK + "\n" + "\n\n".join(parts) + "\n" if parts else None

    def remember(text):
        os.makedirs(os.path.dirname(last_path), exist_ok=True)
        with open(last_path + ".tmp", "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(last_path + ".tmp", last_path)

    text = composed()
    if text is None:
        return None
    if os.path.islink(target):
        where = os.path.realpath(target)
        if not any(where.startswith(os.path.realpath(d) + os.sep) for d in (config.ENGINE, instance)):
            return "own"
    elif os.path.exists(target):
        with open(target, encoding="utf-8") as f:
            current = f.read()
        if not current.startswith(GLOBAL_MARK):
            return "own"
        if current == text:  # exactly what would be written: ours, whatever the record says
            remember(text)
            return "composed"
        last = None
        if os.path.exists(last_path):
            with open(last_path, encoding="utf-8") as f:
                last = f.read()
        if current != last:  # edited since this wrote it (or nothing on record)
            added = current[len(last):] if last is not None and current.startswith(last) else None
            if last is None:  # cannot tell stale from edited: keep a copy, then write as usual
                keep_copy(instance, current, say)
            elif added is not None:
                if added.strip():
                    old = open(mine, encoding="utf-8").read() if os.path.exists(mine) else ""
                    with open(mine, "a", encoding="utf-8") as f:
                        f.write(("" if not old or old.endswith("\n") else "\n") + added.strip("\n") + "\n")
                    say("~/.claude/CLAUDE.md 結尾加的內容已併進實例的 CLAUDE.global.md")
                text = composed()
            else:
                if keep_copy(instance, current, say):
                    say("~/.claude/CLAUDE.md 被改過（不只是在結尾加行），沒有覆寫")
                return "edited"
        if current == text:
            remember(text)
            return "composed"
    os.makedirs(os.path.dirname(target), exist_ok=True)
    tmp = target + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, target)  # a link is replaced by the file, never written through
    remember(text)
    say("~/.claude/CLAUDE.md 由引擎和實例的全域說明組成")
    return "composed"


def repair_links(instance, say, ssh=True):
    """Point Claude's memory folders and ~/.ssh/config at the instance. {"memory": ..., "ssh": ..., "twice": [...]}.
    The engine's own paths (knowledge/ and the rest of MOVED) are adopt_all()'s, under the engine's lock. ssh=False
    (install.sh): ~/.ssh/config left alone here; the installer does it with a backup and its own checks."""
    out = {"memory": None, "ssh": None, "twice": []}
    host = _host()
    try:
        out["global_md"] = compose_global(instance, say)
    except Exception as e:  # a file that is not UTF-8, for one: never stops the links
        out["global_md"] = "error: %s" % e
    target = os.path.join(instance, "memory")
    if os.path.isdir(target):
        for link in memory_links():
            if os.path.islink(link) and os.path.realpath(link) == os.path.realpath(target):
                continue
            out["twice"] += merge_into(link if os.path.exists(link) else "", target, host, say)
            if os.path.islink(link):
                os.remove(link)
            elif os.path.isdir(link):
                os.rename(link, "%s.before-instance-%d" % (link, time.time()))  # never deleted: kept as it was
            os.makedirs(os.path.dirname(link), exist_ok=True)
            os.symlink(target, link)
            say("記憶資料夾改指到實例：%s" % link)
        out["memory"] = "instance"
    sshc = os.path.join(instance, "ssh_config")
    cfg = os.path.realpath(os.path.expanduser("~/.ssh/config"))  # a link stays a link: write where it points
    if ssh and os.path.isfile(sshc) and os.path.getsize(sshc) > 0 and os.path.isfile(cfg):
        with open(cfg) as f:
            text = f.read()
        line = "Include %s" % sshc
        new = text if line in text else "%s\n\n%s" % (line, text)
        if new and not new.endswith("\n"):  # a line added later with `>>` must not run into the last one
            new += "\n"
        if new != text:
            tmp = cfg + ".tmp"
            with open(tmp, "w") as f:
                f.write(new)
            os.chmod(tmp, 0o600)
            os.replace(tmp, cfg)
            say("~/.ssh/config 改成引用實例的 ssh_config" if line not in text else "~/.ssh/config 補上結尾的換行")
        out["ssh"] = "instance"
    return out


def new_skills(say):
    """The engine's skills that have no link in ~/.claude/skills yet made now (plugin.link_skills, add_only): the
    engine is updated by update.py, this sync or home-pull, none of which runs install.sh, and a release that brings
    a new skill must not leave doctor saying 「缺少 skill」 until someone does (manager 2026-10-16, skills/tour).
    Only missing links (a broken one is apply's to make again), and only from this machine's engine (a worktree's
    home-pull links nothing to itself). With settings that cannot be read, a plugin's skill waits and a skill no
    plugin owns (tour) is still linked. Never raises. {"made": [...]}, {"skipped": why} or {"error": ...}."""
    try:
        why = not_this_engine()
        if why:
            return {"skipped": why}
        from . import plugin
        got = plugin.link_skills(config.ENGINE, add_only=True)
    except Exception as e:
        return {"error": "%s: %s" % (type(e).__name__, e)}
    if got["made"]:
        say("補上 skill 連結：%s" % "、".join(got["made"]))
    return {"made": got["made"]}


STUCK_TELL = 2 * 3600


def tell(text):
    """Push a notification (altention dc notify); quietly nothing if it cannot."""
    try:
        subprocess.run([os.path.join(config.ENGINE, "bin", "altention"), "dc", "notify", text], stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, timeout=60)
    except Exception:
        pass


def remember_bot():
    """Write down where the bot runs (STATE/bot.json) while the settings are readable: altention dc notify
    uses it when they are not, since that is when it reports the problem."""
    try:
        name, m, here = config.bot_machine()
        if name:
            path = os.path.join(os.path.dirname(status_path()), "bot.json")
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path + ".tmp", "w") as f:
                json.dump({"name": name, "ssh": m.get("ssh", []), "here": bool(here)}, f)
            os.replace(path + ".tmp", path)
    except Exception:
        pass


def not_this_engine():
    """Why this checkout must not sync this machine (another checkout), or "": update.py's rules (no machine list,
    so a broken list never stops the sync); update.py itself unloadable: only the worktree test (manager
    2026-10-06: from a worktree nothing is done and no state is written)."""
    try:
        from altention import update as altupdate
        kind, why = altupdate.verdict()
        return why if kind == "other" else ""
    except Exception:
        dotgit = os.path.join(os.path.realpath(config.ENGINE), ".git")
        return "%s 是 git worktree，不是這台的引擎" % config.ENGINE if os.path.isfile(dotgit) else ""


def run(say=lambda text: None):
    """The whole job, as altention/cron_sync.py's cron calls it. Never raises; returns and writes the status. From another
    checkout: nothing done, nothing written."""
    why = not_this_engine()
    if why:
        return {"skipped": why}
    status = {"at": time.time(), "machine": None}
    # Where the instance is needs no machine list; the machine's name does. A broken machines.json pushed into the
    # instance must not stop the instance's own sync: that sync is the way its fix arrives (#code-review-2,
    # manager 2026-10-06; the repair path never depends on settings that may be what broke).
    instance = None
    try:
        instance = config.instance_dir()
    except Exception as e:
        status["config_error"] = "%s: %s" % (type(e).__name__, e)
    try:
        status["machine"] = config.this_machine()
    except Exception as e:
        status.setdefault("config_error", "%s: %s" % (type(e).__name__, e))
        _alert_plain("sync", "machine-list", "讀不出這台的名字（%s: %s），實例照樣同步（提交訊息寫 ?）" % (type(e).__name__, e))
    try:
        remember_bot()
    except Exception:
        pass
    moved = set()
    if instance:
        try:
            status["links"] = repair_links(instance, say)
            moved = set(k for k in SHARED if status["links"].get(k) == "instance")
        except Exception as e:
            status["links"] = {"error": "%s: %s" % (type(e).__name__, e)}
    # the instance first: what it holds (the switch, owned_by_instance) is decided by its own content, and when
    # the engine's update takes its copies away (stage 2.3b), the instance already has them
    if instance and not os.path.isdir(os.path.join(instance, ".git")):
        status["instance"] = {"at": time.time(), "ok": False, "error": "實例 %s 不是 git repo，寫進去的記憶不會同步到別台" % instance}
    elif instance:
        try:  # folders in use there and the settings: never removed by a sync
            held = SHARED + list(SETTINGS) + [BACKUP] + owns_list(instance)
        except Exception:
            held = list(INSTANCE_SHARED)
        status["instance"] = sync_repo(instance, INSTANCE_SHARED, "sync: memory/knowledge @ %s" % (status["machine"] or "?"), say,
                                       before=lambda repo: instance_paths(instance), protected=held, instance=True)
    paths = {"states": {}, "twice": []}

    head = []  # the engine's HEAD before this round (C-09: a move here restarts the bot as update.py's does)

    def before(repo):  # under the engine's lock: what the instance holds leaves the engine before anything is committed
        if repo is not None:
            head[:] = [repo.git("rev-parse", "HEAD", timeout=20).stdout.strip()]
        if instance:
            try:  # a step that fails only stops itself: the engine's own sync goes on
                paths["states"], paths["twice"] = adopt_all(instance, say)
            except Exception as e:
                _alert_plain("sync", "adopt", "把引擎裡的資料夾交給實例這一步出錯（%s: %s），引擎照樣同步" % (type(e).__name__, e))
        return [k for k in SHARED if k not in moved and paths["states"].get(k, "engine") == "engine"
                and not os.path.islink(os.path.join(config.ENGINE, k))]

    def after(repo):  # again after the update: it may just have taken the engine's copies away, or brought late files
        if instance:
            try:
                paths["states"], twice = adopt_all(instance, say)
                paths["twice"] += twice
            except Exception as e:
                _alert_plain("sync", "adopt", "更新之後交給實例這一步出錯（%s: %s），引擎照樣同步" % (type(e).__name__, e))
        engine_moved(repo, head[0] if head else "")

    # who moves the engine (3a.10, manager 2026-10-06): altention/update.py, behind its syntax check, while it is
    # alive; when it has run before but not worked for three of its rounds (update.stale_of), this sync moves the engine itself (no check: the way a
    # fix still arrives while update.py is what broke) and says so. Never run yet (no cron line): as before, quietly.
    # Only when the instance's core.update is "branch": notify and release never take the branch (manager 2026-10-14).
    mode, seen = update_mode()[0], None  # update.py tells when the mode cannot be read; here only what it allows
    try:  # update.py itself broken must not stop this one too: then this sync moves the engine, and says so
        from altention import update as altupdate
        alive = altupdate.last_alive()
        seen = alive["at"] if alive else altupdate.last_ok("update")  # alive: every round that came to its end
        pull = seen is None or time.time() - seen > altupdate.stale_of("engine-update")
        if seen is not None and pull and mode == "branch":
            altupdate.alert("sync", "update-stale", "引擎更新（update.py）已經 %d 分鐘沒有跑完一輪，由同步代為更新引擎（沒有語法檢查）"
                            % ((time.time() - seen) // 60))
    except Exception as e:
        altupdate, pull = None, True
        _alert_plain("sync", "update-broken", "載不進或用不了 altention/update.py（%s: %s），%s" % (
            type(e).__name__, e, "由同步代為更新引擎" if mode == "branch" else "core.update 不是 branch：同步不代為更新"))
    if pull and mode != "branch":  # notify and release never take the branch, not even standing in (manager 2026-10-14)
        if seen is not None:
            _alert_plain("sync", "update-stale-held", "引擎更新沒在跑，core.update 是 %s：同步不代為拉上游分支，引擎照舊" % mode)
        pull = False
    # the refused hash read the one way every path that pulls the engine reads it (the file itself, not update.py:
    # it may be what broke); compared after a fetch, under the lock (sync_repo)
    refused = refused_hash() if pull else ""
    status["engine_pulled_by"] = "sync" if pull else "update"
    if is_checkout():
        status["engine"] = sync_repo(config.ENGINE, [], "sync: memory/knowledge @ %s" % (status["machine"] or "?"), say,
                                     before=before, after=after, protected=SHARED, pull=pull, refuse=refused, push_out=False)
        install_pending()  # the lock let go: pip for a move this round (or an earlier one) marked
        eng = status["engine"]
        if mode != "branch" and type(eng.get("ahead")) is int and eng["ahead"] > 0:
            try:  # C-07: rollbacks the supervisor keeps here on releases are not to push; doctor says which are which
                backs, others = local_commits(Repo(config.ENGINE), "@{u}")
                eng["rollbacks"] = len(backs) if backs is not None else None
                # with a rollback's subject but not only a rollback (amended with a file of the owner's, say):
                # not to push as it is either (#code-review-2 of 7daa3df)
                eng["rollbacks_unsure"] = len([h for h, looks in others if looks]) if backs is not None else None
            except Exception as e:
                eng["rollbacks"] = eng["rollbacks_unsure"] = None
                _alert_plain("sync", "rollbacks", "數不出引擎有幾個退回 commit（%s: %s）" % (type(e).__name__, e))
    else:  # unpacked from an archive: the instance's folders are still linked; nothing to commit, pull or push
        before(None)
        status["engine"] = {"at": time.time(), "ok": True, "error": None, "checkout": False,
                            "note": "引擎不是 git checkout（解開的壓縮檔）：引擎這一段不同步"}
    if (status.get("engine") or {}).get("refused"):
        status["engine_refused"] = status["engine"]["refused"]
    try:  # the plugins' data folders marked sync: timer (C-26); one's trouble stops nothing else
        status["data"] = sync_data(status["machine"] or "?", say, instance)
    except Exception as e:
        status["data"] = {"error": "%s: %s" % (type(e).__name__, e)}
    if instance:  # again now that both repos are pulled: an engine that came first never meets an old instance half
        try:
            g = compose_global(instance, say)
            if isinstance(status.get("links"), dict):
                status["links"]["global_md"] = g
        except Exception as e:  # run() never raises (a file that is not UTF-8, for one)
            if isinstance(status.get("links"), dict):
                status["links"]["global_md"] = "error: %s" % e
    status["skills"] = new_skills(say)  # after the engine's part: an update (this round's or update.py's) may bring one
    if isinstance(status.get("links"), dict) and "error" not in status["links"]:
        status["links"]["engine_paths"] = paths["states"]
        status["links"]["twice"] += paths["twice"]
        status["links"]["knowledge"] = "instance" if paths["states"].get("knowledge") == "link" else "engine"
    previous = config.read_json(status_path()) or {}
    for part in ("engine", "instance"):  # failing for hours (stuck rebase or anything else): say so once, not only in a file
        now, before = status.get(part) or {}, previous.get(part) or {}
        if now and not now.get("ok") and now.get("error"):
            now["failing_since"] = before.get("failing_since") or now["at"]
            now["told"] = before.get("told")
            if not now["told"] and now["at"] - now["failing_since"] > STUCK_TELL:
                tell("⚠️ %s 的%s同步失敗超過 %d 小時：%s" % (status.get("machine") or "?", {"engine": "引擎", "instance": "實例"}[part],
                                                        STUCK_TELL // 3600, now.get("error")))
                now["told"] = now["at"]
    if (status.get("engine") or {}).get("ok") and (not instance or (status.get("instance") or {}).get("ok")):
        try:
            altupdate.mark_ok("sync")  # update.py and the bot's machine look at it
        except Exception:
            pass
    try:
        os.makedirs(os.path.dirname(status_path()), exist_ok=True)
        tmp = status_path() + ".tmp"
        with open(tmp, "w") as f:
            json.dump(status, f, ensure_ascii=False, indent=1)
        os.replace(tmp, status_path())
    except OSError:
        pass
    return status


def timer_data():
    """[(name, path)]: the data of the plugins on here that plugin.json marks sync "timer" (the default)."""
    from altention import plugin
    rows, _problems = plugin.on_this_machine()
    out = []
    for name, folder, d, where, _why in rows:
        if d is None or where != "on" or name == "core":
            continue
        for item in d.get("data") or []:
            if item.get("sync", "timer") != "timer":
                continue
            try:
                out.append(("%s/%s" % (name, item["name"]), plugin.expand(item["path"], folder)))
            except Exception:  # a setting it names is not there: not this plugin's data on this machine
                continue
    return out


def own_repo(path):
    """True when path is the top of a git repo of its own (not a folder inside the instance, the engine or another)."""
    if not os.path.isdir(os.path.join(path, ".git")):
        return False
    r = Repo(path).git("rev-parse", "--show-toplevel", timeout=20)
    return r.returncode == 0 and os.path.realpath(r.stdout.strip()) == os.path.realpath(path)


OUR_HOOK = "# altention pre-push (ops/remote.py)"  # ops/remote.py HOOK_MARK, the first line of the hook it puts in


def has_our_hook(path):
    """True when the pre-push hook git runs for this repo is ours (ops/remote.py put_hook: attached by us)."""
    r = Repo(path).git("rev-parse", "--git-path", "hooks/pre-push", timeout=20)
    hook = r.stdout.strip()
    if r.returncode != 0 or not hook:
        return False
    try:
        with open(hook if os.path.isabs(hook) else os.path.join(path, hook), encoding="utf-8", errors="replace") as f:
            return OUR_HOOK in f.read()
    except OSError:
        return False


def push_guard(repo):
    """Why a data repo is not pushed this round, or None: ops/remote.py's pre-push check (never a public repo nor the
    engine's), run here as well before the hook does. Exit 10: stopped; any other failure lets the push go (our hook
    is in and checks again), as the hook itself does (manager 2026-10-16)."""
    if has_upstream(repo) and not commits_ahead(repo):
        return None  # nothing to push: not asked
    url = repo.git("remote", "get-url", "origin", timeout=20).stdout.strip()
    try:
        r = subprocess.run(limited(["python3", os.path.join(config.ENGINE, "ops", "remote.py"), "pre-push", "origin", url], 120),
                           cwd=repo.path, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, timeout=130,
                           stdin=subprocess.DEVNULL)
        code, said = r.returncode, (r.stderr.strip() or r.stdout.strip())
    except (OSError, subprocess.SubprocessError) as e:
        code, said = 1, str(e)
    if code == 0:
        return None
    if code == 10:
        return said.replace("altention：", "", 1)[:300] or "沒有推：遠端沒通過檢查"
    return None


def sync_data(machine, say, instance=None):
    """{name: result} for each timer data folder: a repo of its own with an origin and our pre-push hook (attached
    by ops/remote.py: one we took on) is committed whole, updated and pushed, under its own lock, like the instance.
    One with no origin, no repo, or no hook of ours (the owner's own repo a setting points at: manager 2026-10-16)
    is left, said in sync.json."""
    out = {}
    skip = set(os.path.realpath(p) for p in (config.ENGINE, instance) if p)
    for name, path in timer_data():
        if os.path.realpath(path) in skip or not os.path.isdir(path):
            continue
        if not own_repo(path):
            out[name] = {"at": time.time(), "ok": True, "error": None, "path": path, "note": "不是自己的 git repo，沒有同步"}
            continue
        if Repo(path).git("remote", "get-url", "origin", timeout=20).returncode != 0:
            out[name] = {"at": time.time(), "ok": True, "error": None, "path": path, "note": "沒有設定遠端，只留在這台"}
            continue
        if not has_our_hook(path):
            out[name] = {"at": time.time(), "ok": True, "error": None, "path": path,
                         "note": "沒接上（沒有我們的 pre-push hook），不同步：要同步就用 ops/remote.py 接上"}
            continue
        out[name] = sync_repo(path, ["."], "sync: %s @ %s" % (name, machine), say, track=True, guard=push_guard)
        out[name]["path"] = path
    return out


def refused_hash(note=None):
    """The upstream hash altention/update.py is refusing, read straight from ~/.local/state/altention/
    last_alive-update (plain JSON), or "". No file: no record. A file that cannot be read or parsed: no record
    either (the engine is pulled as before), and a line in the alerts file. The same reading is in update.py and
    both cron launchers; a test feeds all four the same files (manager 2026-10-06)."""
    path = os.path.expanduser("~/.local/state/altention/last_alive-update")
    try:
        with open(path) as f:
            got = json.load(f)
    except FileNotFoundError:
        return ""
    except (OSError, ValueError) as e:
        (note or (lambda t: _alert_plain("sync", "alive-unreadable", t)))("讀不出 %s（%s），當成沒有被擋的紀錄" % (path, e))
        return ""
    if not isinstance(got, dict):  # [] or a bare value: no record either, and said like an unreadable one
        (note or (lambda t: _alert_plain("sync", "alive-unreadable", t)))("%s 不是物件，當成沒有被擋的紀錄" % path)
        return ""
    b = got.get("blocked")
    return b.get("hash", "") if isinstance(b, dict) and isinstance(b.get("hash"), str) else ""


UPDATE_MODES = ("notify", "release", "branch")  # update.py's: who may take what (manager 2026-10-14)
RELEASE_TAG = re.compile(r"^v([0-9]+)\.([0-9]+)\.([0-9]+)$")


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


def latest_release(repo):
    """(tag, commit) of the highest vX.Y.Z tag the repo has (update.py's latest_release), or None."""
    best = None
    for tag in repo.git("tag", "-l", "v*", timeout=20).stdout.split():
        m = RELEASE_TAG.match(tag)
        if m and (best is None or tuple(map(int, m.groups())) > best[0]):
            best = (tuple(map(int, m.groups())), tag)
    if best is None:
        return None
    commit = repo.git("rev-parse", "-q", "--verify", best[1] + "^{commit}", timeout=20).stdout.strip()
    return (best[1], commit) if commit else None


def stacked_on(repo, refuse, up="@{u}"):
    """Is the refused hash in the upstream's history (the upstream itself or below it) and not yet in HEAD? The
    rule for the paths that run while update.py is checking (home-pull, deploy.sh): then the new upstream is
    update.py's to judge, whatever was put on top (manager 2026-10-07; DESIGN 7.1). A hash the repo does not know
    is in no history."""
    def under(a, b):
        return repo.git("merge-base", "--is-ancestor", a, b, timeout=20).returncode == 0
    return bool(refuse) and under(refuse, up) and not under(refuse, "HEAD")


def held_back(repo, then, note=None, strict=False, up="@{u}"):
    """Why not to pull `repo` (the engine) now: the hash update.py refused (refused_hash, the one reading) is in the
    upstream and not in HEAD (stacked_on: a commit put on top of it is update.py's to judge), or that cannot be told
    (fetch failed; with `strict`, a record that cannot be read either); "" to go on. `then`: what the caller can do
    about it. For bin/home-pull and deploy.sh (strict) (manager 2026-10-07; DESIGN 7.1). `up`: what would be taken
    (home-pull in release mode: the release's commit)."""
    unread = []

    def said(text):
        unread.append(text)
        (note or (lambda t: _alert_plain("sync", "alive-unreadable", t)))(text)
    refuse = refused_hash(said)
    if strict and unread:
        return "%s：%s" % (unread[-1], then)
    if not refuse:
        return ""
    if repo.git("fetch", "-q").returncode != 0:
        return "fetch 失敗，不知道上游是不是被擋的 %s：%s" % (refuse[:12], then)
    if not stacked_on(repo, refuse, up):
        return ""  # a fix that replaced it, or already in HEAD (a person took it): as usual
    up = repo.git("rev-parse", up, timeout=20).stdout.strip()
    if up != refuse:  # put on top of it (a fix, or not): update.py's to judge, no flag offered (#code-review-2)
        return "上游 %s 疊在被擋的 %s 上面，等 update 那行判斷；%s" % (up[:12], refuse[:12], "沒有部署" if strict else "沒有更新")
    try:  # the reason, for the message only
        with open(os.path.expanduser("~/.local/state/altention/last_alive-update")) as f:
            reason = (json.load(f).get("blocked") or {}).get("why") or ""
    except Exception:
        reason = ""
    return "上游 %s 被擋（%s）：%s" % (refuse[:12], " ".join(reason.split())[:160] or "原因沒有記下", then)


SETTINGS = ("machines.json", "plugins.json")  # the instance's settings, checked before the instance is moved
ENTRIES = {"machines.json": ("machines",), "plugins.json": ("plugins",)}  # sections whose entries are objects


class Unread(Exception):
    """The tree to check could not be read this time (git failed or timed out): not known, so not taken this round,
    and not held as bad either (manager 2026-10-07)."""


def settings_problem(repo, rev):
    """Why the instance's settings at `rev` are not to be taken, or "": each of SETTINGS that is there is UTF-8 JSON,
    an object, with a format that is a whole number from 1 (a newer one than config.FORMAT is taken, as config reads
    it: an engine rolled back must work with newer settings), and its sections (top-level keys but "format" and
    "_…") and their ENTRIES are objects; and machines.json, if HEAD has one, is not deleted (config would quietly
    read "this machine alone"; a plugins.json deleted only means the defaults). Nothing about the content: a wrong
    value comes in, and its fix the same way (manager 2026-10-06, 10-07). Raises Unread when git cannot say."""
    for name in SETTINGS:
        r = repo.git("ls-tree", "--name-only", rev, "--", name, timeout=60)
        if r.returncode != 0:
            raise Unread("讀不到 %s 的 %s（%s）" % (rev[:12], name, (r.stderr.strip() or r.stdout.strip())[:120]))
        if not r.stdout.strip():
            if name == "machines.json":
                here = repo.git("ls-tree", "--name-only", "HEAD", "--", name, timeout=60)
                if here.returncode != 0:
                    raise Unread("讀不到 HEAD 的 %s（%s）" % (name, (here.stderr.strip() or here.stdout.strip())[:120]))
                if here.stdout.strip():
                    return "machines.json 被刪了（這台有、上游沒有：接上之後會變成只有這一台）"
            continue  # not in the tree, and not here either: nothing to parse (config reads it as no file)
        try:
            raw = subprocess.run(limited(["git", "-C", repo.path, "show", "%s:%s" % (rev, name)], 60),
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=70, check=True,
                                 pass_fds=(repo.lock_fd,) if repo.lock_fd is not None else ()).stdout
            data = json.loads(raw.decode("utf-8"))
        except (OSError, subprocess.SubprocessError) as e:
            raise Unread("讀不到 %s 的 %s（%s）" % (rev[:12], name, str(e)[:120]))
        except ValueError as e:  # UnicodeDecodeError is one
            return "%s 讀不出來（%s）" % (name, str(e)[:120])
        why = settings_data_problem(name, data)
        if why:
            return why
    return ""


def settings_data_problem(name, data):
    """Why one of SETTINGS, read as `data`, is not to be taken, or "" (settings_problem's checks of its shape)."""
    if not isinstance(data, dict):
        return "%s 不是 JSON 物件" % name
    fmt = data.get("format", 1)
    if type(fmt) is not int or fmt < 1:
        return "%s 的格式 %s 不是 1 以上的整數" % (name, json.dumps(fmt)[:20])
    for key, value in data.items():
        if key != "format" and not key.startswith("_") and not isinstance(value, dict):
            return "%s 的 %s 不是物件" % (name, key)
    for key in ENTRIES[name]:
        for entry, value in (data.get(key) or {}).items():
            if not entry.startswith("_") and not isinstance(value, dict):
                return "%s 的 %s.%s 不是物件" % (name, key, entry)
    return ""


def instance_paths(instance):
    """INSTANCE_SHARED but a settings file that does not read well here: committed, every other machine would hold
    the instance back (settings_problem); left out until it is fixed, said in the alerts. Known limit (manager
    2026-10-16): the file is read here and added a few seconds later; one half saved in between is committed, and
    the other machines hold that version back (said in their alerts) until the next round commits it whole."""
    out = []
    for p in INSTANCE_SHARED:
        if p in SETTINGS and os.path.isfile(os.path.join(instance, p)):
            try:
                with open(os.path.join(instance, p), "rb") as f:
                    why = settings_data_problem(p, json.loads(f.read().decode("utf-8")))
            except (OSError, ValueError) as e:
                why = "%s 讀不出來（%s）" % (p, str(e)[:120])
            if why:
                _alert_plain("sync", "settings-broken", "%s：這一輪沒有提交（別台會因此擋下實例的更新），修好之後下一輪就會提交" % why)
                continue
        out.append(p)
    return out


def held_path():
    return os.path.join(state_dir(), "instance-held")


def instance_held(note=None):
    """The instance upstream held back by the settings check: {"hash", "why", "since", "at", "ahead"}, or {}. The one
    reading: update_instance and doctor read it here. A file that cannot be read is no record (the next upstream is
    checked all the same), said through `note` (default: the alerts file)."""
    try:
        with open(held_path()) as f:
            got = json.load(f)
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as e:
        (note or (lambda t: _alert_plain("sync", "held-unreadable", t)))("讀不出 %s（%s），當成沒有被擋的紀錄" % (held_path(), e))
        return {}
    if not isinstance(got, dict) or not isinstance(got.get("hash"), str) or \
            type(got.get("since", 0)) not in (int, float) or type(got.get("at", 0)) not in (int, float):
        (note or (lambda t: _alert_plain("sync", "held-unreadable", t)))("%s 的內容不對，當成沒有被擋的紀錄" % held_path())
        return {}
    return got


def _write_held(record):
    if not record:
        try:
            os.remove(held_path())
        except FileNotFoundError:
            pass
        return
    os.makedirs(state_dir(), exist_ok=True)
    with open(held_path() + ".tmp", "w") as f:
        json.dump(record, f, ensure_ascii=False)
    os.replace(held_path() + ".tmp", held_path())


def update_instance(repo, take=False):
    """update() for the instance, its upstream's settings checked first (settings_problem; manager 2026-10-06).
    (code, note, held): held says why the upstream was not taken, "" when it was or nothing was to take; each held
    round writes its line in the alerts file. Held: the branch stays where it is, this machine's commits stay here
    (not rebased onto it, so not pushed). Content that does not pass is recorded (instance_held) and that upstream
    stays held, not checked again, until another one comes (a fix pushed on top is checked and taken; the next round
    pushes what waited) or a person takes it: take=True (`ops/instance.py pull --take-refused`, which apply.py runs)
    pulls without the check, says in `note` that it took the held one, and clears the record once taken. A tree
    that cannot be read is held this round only, never recorded (manager 2026-10-07). What is taken is the very
    commit checked, not @{u} read again."""
    if repo.git("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}", timeout=20).returncode != 0:
        code, note = update(repo)  # no upstream: nothing to check (update says why, or that none is needed)
        return code, note, ""
    r = repo.git("fetch", "-q")
    if r.returncode != 0:
        return 1, "fetch 失敗：%s" % (r.stderr.strip() or r.stdout.strip())[:300], ""
    up = repo.git("rev-parse", "@{u}", timeout=20).stdout.strip()
    if not up:
        return 1, "讀不到實例的上游是哪個 commit，沒有更新", ""
    old = instance_held()
    if take or repo.git("merge-base", "--is-ancestor", up, "HEAD", timeout=20).returncode == 0:
        code, note = update(repo, to=up)
        if code == 0:
            if take and old and repo.git("merge-base", "--is-ancestor", old["hash"], "HEAD", timeout=20).returncode == 0:
                note = "越過了被擋的 %s（%s）" % (old["hash"][:12], old.get("why") or "原因沒有記下")
            _write_held({})  # taken by a person, or nothing new to take (already in HEAD)
        return code, note, ""
    try:
        why = (old.get("why") or "原因沒有記下") if old.get("hash") == up else settings_problem(repo, up)
    except Unread as e:
        held = "這一輪%s，沒有接上實例上游 %s（不算被擋，下一輪再看）" % (e, up[:12])
        _alert_plain("sync", "instance-unread", held)
        return 0, None, held
    if not why:
        code, note = update(repo, to=up)
        if code == 0 and old:
            _write_held({})
        return code, note, ""
    ahead = repo.git("rev-list", "--count", "%s..HEAD" % up, timeout=20).stdout.strip() or "?"
    now = time.time()
    _write_held({"hash": up, "why": why, "ahead": ahead, "at": now,
                 "since": old.get("since", now) if old.get("hash") == up else now})
    held = ("實例上游 %s 被擋（%s）：這台的實例有 %s 個 commit 還沒推出去，等上游修好（修好後下一輪自動推）；"
            "確定是檢查誤擋時，從控制端跑 python3 ops/apply.py %s" % (up[:12], why, ahead, _host()))
    _alert_plain("sync", "instance-held", held)
    return 0, None, held


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


def engine_moved(repo, before):
    """C-09: after this lock's work moved the engine's HEAD from `before` (the sync standing in for update.py,
    home-pull): altention/update.py's after_move, so the bot restarts into it, or its packages are marked to come
    first (install_pending, once the lock is let go). Never raises; update.py not loading is said. True when the
    bot's restart flag was written."""
    now = repo.git("rev-parse", "HEAD", timeout=20).stdout.strip()
    if not before or not now or before == now:
        return False
    try:
        from altention import update as altupdate
        return altupdate.after_move(repo, before, now)
    except Exception as e:
        _alert_plain("sync", "after-move", "引擎動到 %s，但沒能裝機器人的套件、寫重啟旗標（%s: %s）：機器人可能還在跑舊版；"
                     "在這台的引擎跑 dcbot/install.sh，再 !restart" % (now[:12], type(e).__name__, e))
        return False


def install_pending():
    """altention/update.py's install_pending (the bot's packages a move needs), outside the git lock; never raises.
    "" or what it did."""
    try:
        from altention import update as altupdate
        return altupdate.install_pending(_alert_plain)
    except Exception as e:
        _alert_plain("sync", "deps", "裝機器人的套件這一步出錯（%s: %s）" % (type(e).__name__, e))
        return ""


def _alert_plain(source, kind, text):
    """One line in the alerts file without altention/update.py (it may be what broke); never raises."""
    write_alert(source, kind, text)


def main(argv):
    """python3 -m altention.sync: one round (its own cron line, 3a.10); 0 when both repos went through."""
    if len(argv) == 3 and argv[0] == "--allow-delete":
        allow_delete(argv[1], argv[2])
        print("下一輪同步會提交 %s 裡的刪除（24 小時內有效，用一次）" % argv[2])
        return 0
    if argv:
        print("用法：python3 -m altention.sync    或    python3 -m altention.sync --allow-delete REPO FOLDER")
        return 2
    st = run(lambda text: print(text))
    if st.get("skipped"):
        print("不同步：%s" % st["skipped"])
        print("ALT-SYNC-DONE")
        return 0
    bad = [p for p in ("engine", "instance") if st.get(p) and not st[p].get("ok")]
    for p in bad:
        print("%s：%s" % (p, st[p].get("error")))
    print("ALT-SYNC-DONE")  # cron_sync.py: this round came to its end (else it fast-forwards by plain git)
    return 1 if bad or st.get("config_error") else 0


if __name__ == "__main__":  # from the engine's folder: python3 -m altention.sync [--allow-delete REPO FOLDER]
    sys.exit(main(sys.argv[1:]))
