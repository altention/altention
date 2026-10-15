#!/usr/bin/env python3
"""deploy.sh --commit: a reviewed commit is checked before it is anywhere the machine runs code from.

Until 2026-10-09 a change outside the bot was first checked out into the live tree and tested there: cron ran the
new altention/, chatlog/, progress/… for the minute or two the tests took (once the tests' own guard stopped a deploy
because the real minute round, already on the new code, wrote into the real home). Now:

  prepare <message> <hash>…  read-only checks, then a candidate commit C in a throwaway worktree under the git
                             dir: HEAD plus what the last of the hashes changed against its base, nothing else.
                             Every commit from main up to it must be among the hashes (each reviewed); the
                             upstream must have nothing this main has not. Prints shell assignments (CAND, C,
                             HEAD0) for deploy.sh, which runs every check inside CAND.
  land <C> <HEAD0>           (deploy.sh holds the git lock) the live branch moves to C by fast-forward, only if C
                             is still the candidate, HEAD is still where it was made and the paths are clean.
  untouched                  after a failure: is the live tree exactly as it was before prepare? Said either way.
  done                       success: the candidate worktree and the kept test output are removed.
  in-place [path…]           the old way (edit in place, then deploy.sh "message") is only for what nothing runs
                             before the bot restarts; prints the uncommitted paths under dcbot/ and the listed
                             ones (from the repo's root) that must go through --commit.

Standard library, Python 3.8. Nothing here writes outside the git dir's altention-deploy/ and the branch move."""
import ast
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))  # dcbot/
KEEP = "altention-deploy"  # under the git dir: candidate/ (the worktree), tests/ (output of a failed run), state.json


def git(*args, cwd=None, check=True):
    r = subprocess.run(["git"] + list(args), cwd=cwd or root(), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                       universal_newlines=True)
    if check and r.returncode != 0:
        raise Stop("git %s：%s" % (" ".join(args[:3]), (r.stderr or r.stdout).strip()[-300:]))
    return r


class Stop(Exception):
    pass


_root = []


def root():
    if not _root:
        r = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=HERE, stdout=subprocess.PIPE, universal_newlines=True)
        _root.append(r.stdout.strip())
    return _root[0]


def keep_dir():
    gd = git("rev-parse", "--git-common-dir").stdout.strip()
    return os.path.join(gd if os.path.isabs(gd) else os.path.join(root(), gd), KEEP)


# ---------------------------------------------------------------- what runs without a restart
def _imports(path):
    """Names of the bot/ modules a Python file imports, wherever in it (a lazy import inside a function too)."""
    try:
        with open(path, encoding="utf-8") as f:
            tree = ast.parse(f.read())
    except (OSError, SyntaxError, ValueError):
        return None
    out = set()
    inside = os.path.basename(os.path.dirname(path)) == "bot"
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            if node.level == 1 and inside:  # from . import a, b / from .a import x
                out.update([mod.split(".")[0]] if mod else [a.name for a in node.names])
            elif node.level == 0 and mod == "bot":  # from bot import a, b
                out.update(a.name for a in node.names)
            elif node.level == 0 and mod.startswith("bot."):
                out.add(mod.split(".")[1])
        elif isinstance(node, ast.Import):
            out.update(a.name.split(".")[1] for a in node.names if a.name.startswith("bot."))
    return out


def live_modules(dcbot=HERE):
    """The files under dcbot/bot/ that something runs without the bot restarting: whatever tools/* (run by hooks,
    by other channels, on other machines) import, and what those import. None when a tool cannot be read: then
    everything in bot/ counts."""
    todo, seen = [], set()
    tools = os.path.join(dcbot, "tools")
    for name in sorted(os.listdir(tools)) if os.path.isdir(tools) else []:
        path = os.path.join(tools, name)
        if os.path.isfile(path):
            found = _imports(path)
            if found is None:
                with open(path, "rb") as f:
                    if f.read(2) == b"#!" or name.endswith(".py"):
                        return None
                continue
            todo += sorted(found)
    while todo:
        mod = todo.pop()
        if mod in seen:
            continue
        path = os.path.join(dcbot, "bot", mod + ".py")
        if not os.path.isfile(path):
            continue
        seen.add(mod)
        found = _imports(path)
        if found is None:
            return None
        todo += sorted(found - seen)
    live = {"dcbot/bot/%s.py" % m for m in seen}
    if live:  # importing any module of the package runs the package's own file first (it puts the engine on the path)
        live.add("dcbot/bot/__init__.py")
    return live


def in_place_ok(path, live):
    """May this path (from the repo's root) be edited in the live tree and deployed the old way? Only what nothing
    runs before the bot restarts: the bot's own modules that no tool imports, tests, documents, and the two files
    a restart or a person picks up (requirements.txt, supervisor.py). Decided by who runs the file, not by where
    it lies: dcbot/tools/*, deploy.sh and the modules the tools import are run as they are, by hooks and cron."""
    path = path.replace(os.sep, "/")
    parts = path.split("/")
    if path.endswith(".md") or "tests" in parts[:-1]:
        return True
    if path in ("dcbot/requirements.txt", "dcbot/supervisor.py") or path.startswith("dcbot/guide/"):
        return True
    if path.startswith("dcbot/bot/") and path.endswith(".py"):
        return live is not None and path not in live
    return False


# ---------------------------------------------------------------- the candidate
def snapshot(paths):
    """What `untouched` compares: HEAD, the whole status, and the bytes of the paths about to change."""
    def sha(p):
        full = os.path.join(root(), p)
        if os.path.islink(full):
            return "link:" + os.readlink(full)
        try:
            with open(full, "rb") as f:
                return hashlib.sha256(f.read()).hexdigest()
        except OSError:
            return None
    return {"head": git("rev-parse", "HEAD").stdout.strip(), "status": git("status", "--porcelain").stdout,
            "files": {p: sha(p) for p in paths}}


def forget_candidate():
    keep = keep_dir()
    cand = os.path.join(keep, "candidate")
    if os.path.exists(cand):
        git("worktree", "remove", "--force", cand, check=False)
    shutil.rmtree(keep, ignore_errors=True)
    git("worktree", "prune", check=False)


def patch_id(a, b, paths):
    diff = subprocess.run(["git", "diff", "--no-renames", "--binary", a, b, "--"] + paths, cwd=root(), stdout=subprocess.PIPE).stdout
    out = subprocess.run(["git", "patch-id", "--stable"], cwd=root(), input=diff, stdout=subprocess.PIPE).stdout.decode()
    return out.split()[0] if out.split() else ""


def behind(landing=False):
    """How many commits the upstream has that this main has not, after a fetch. They are to come in by home-pull
    (through this machine's own checks) before a deploy: found only when the push is refused, they were put under
    the commit just landed and the tree that went out was one no check had run on (#code-review-2 2026-10-09,
    manager). No upstream: 0. A fetch that fails (no network): before the candidate is made it is said and taken
    for 0 (the landing asks again); at the landing it stops the deploy: not known is not "nothing new". The
    fetch runs under the lock every git job here shares: deploy.sh holds it at the landing, here it is taken for
    the fetch alone (a fetch beside the sync's could fail one of the two on a locked ref)."""
    if git("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}", check=False).returncode != 0:
        return 0
    lock = None
    if not landing:
        import fcntl
        gd = git("rev-parse", "--git-common-dir").stdout.strip()
        lock = open(os.path.join(gd if os.path.isabs(gd) else os.path.join(root(), gd), "agent-git.lock"), "a")
        for _ in range(120):
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                time.sleep(1)
        else:
            lock.close()
            raise Stop("120 秒內等不到 git 鎖（別的同步或部署還在跑），稍後再部署")
    try:
        try:
            r = subprocess.run(["git", "fetch", "-q"], cwd=root(), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               universal_newlines=True, timeout=120)
            failed = r.returncode != 0 and (r.stderr.strip()[-200:] or "結束碼 %d" % r.returncode)
        except subprocess.TimeoutExpired:
            failed = "120 秒沒有結束"
    finally:
        if lock:
            lock.close()
    if failed and landing:
        raise Stop("查不了 origin 有沒有比這台新（fetch 失敗：%s），所以沒有換上去：正式樹不動。網路好了再重跑一次部署" % failed)
    if failed:
        sys.stderr.write("（查不了 origin 有沒有比這台新：fetch 失敗：%s。先繼續做候選和檢查；換上去之前會再查一次，那時還查不了就停）\n" % failed)
        return 0
    return int(git("rev-list", "--count", "HEAD..@{u}").stdout.strip() or 0)


NEWER = "origin 比這台的 main 新（%d 個 commit）：先 bin/home-pull 把它們接進來，再部署（接進來之後的樹才是檢查要跑的那棵）"


def prepare(message, commits):
    head = git("rev-parse", "HEAD").stdout.strip()
    listed = []
    for commit in commits:
        r = git("rev-parse", "--verify", "-q", commit + "^{commit}", check=False)
        if r.returncode != 0:
            raise Stop("找不到 commit %s" % commit)
        if r.stdout.strip() not in listed:
            listed.append(r.stdout.strip())
    newer = behind()
    if newer:
        raise Stop(NEWER % newer)
    # the one the others lead up to: what goes live is its tree's changes, so every commit on the way to it from
    # main must have been named, each of them reviewed (one stacked on an unreviewed one took that one live too)
    tips = [h for h in listed if all(git("merge-base", "--is-ancestor", o, h, check=False).returncode == 0 for o in listed)]
    if not tips:
        raise Stop("列出的 commit 不在同一條線上（要一個接一個疊起來的）：%s" % "、".join(h[:7] for h in listed))
    full = tips[0]
    short = full[:7]
    base = git("merge-base", head, full).stdout.strip()
    if base == full:
        raise Stop("%s 已經在 main 裡，沒有東西要部署" % short)
    way = git("rev-list", "--reverse", "%s..%s" % (base, full)).stdout.split()
    unnamed = [h for h in way if h not in listed]
    if unnamed:
        raise Stop("main 到 %s 之間還有沒列出的 commit，會跟著一起上線：\n%s\n  每個都審過的話全部列出來（%s）；"
                   "沒審過的不能上線" % (short, "\n".join("    " + git("log", "-1", "--format=%h %s", h).stdout.strip()[:100] for h in unnamed),
                                   " ".join("--commit " + h[:7] for h in way)))
    already = [h for h in listed if h not in way]
    if already:
        raise Stop("%s 已經在 main 裡，不用列" % "、".join(h[:7] for h in already))
    # --no-renames everywhere: a rename is a file gone and a file new, and both are in the list (with renames
    # followed only the new name was, the old file stayed in the candidate and every check passed: #code-review)
    paths = [p for p in git("diff", "--no-renames", "--name-only", "-z", base, full).stdout.split("\0") if p]
    if not paths:
        raise Stop("%s 對它的基底 %s 沒有任何改動" % (short, base[:7]))
    between = git("log", "--oneline", "%s..%s" % (base, head), "--", *paths).stdout.strip()
    if between:  # reviewed against another state of these files than the one it would land on
        raise Stop("%s 的基底 %s 之後，main 上有別的 commit 動到同樣的檔案，審過的內容和現在的 main 對不上：\n%s\n"
                   "  請把 %s 接到現在的 main 上、重新送審" % (short, base[:7], between, short))
    dirty = git("status", "--porcelain", "--", *paths).stdout.rstrip("\n")
    if dirty:
        raise Stop("正式樹裡這次要換的檔有還沒提交的修改，不覆蓋：\n%s" % dirty)
    forget_candidate()  # what the last deploy left (kept only when it failed)
    keep = keep_dir()
    os.makedirs(keep, exist_ok=True)
    before = snapshot(paths)
    cand = os.path.join(keep, "candidate")
    git("worktree", "add", "-q", "--detach", cand, head)
    there = set(p for p in git("ls-tree", "-r", "--name-only", "-z", full, "--", *paths).stdout.split("\0") if p)
    if there:
        git("checkout", full, "--", *sorted(there), cwd=cand)
    gone = [p for p in paths if p not in there]
    if gone:
        git("rm", "-q", "--", *gone, cwd=cand)
    if "reviewed" not in message or any(h[:7] not in message for h in way):
        message = "%s (reviewed %s)" % (message.rstrip(), ", ".join(h[:7] for h in way))
    git("commit", "-q", "-m", message, cwd=cand)
    c = git("rev-parse", "HEAD", cwd=cand).stdout.strip()
    # Checked without the list of paths (a list made wrongly passed every check made from it):
    changed = sorted(p for p in git("diff", "--no-renames", "--name-only", "-z", head, c).stdout.split("\0") if p)
    if base == head:  # nothing on main since the base: the candidate is the reviewed tree itself
        same = git("rev-parse", c + "^{tree}").stdout == git("rev-parse", full + "^{tree}").stdout
    else:  # main moved elsewhere: the candidate differs from HEAD by exactly what the review differs from its base
        same = patch_id(base, full, []) == patch_id(head, c, []) != ""
    same = same and changed == sorted(paths) and \
        git("ls-tree", "-r", full, "--", *paths).stdout == git("ls-tree", "-r", c, "--", *paths).stdout
    if not same:
        raise Stop("候選 commit 和審過的 %s 對不上（這不該發生）：候選留在 %s" % (short, cand))
    with open(os.path.join(keep, "state.json"), "w", encoding="utf-8") as f:
        json.dump({"head": head, "candidate": c, "reviewed": full, "listed": way, "base": base, "paths": paths, "before": before}, f)
    sys.stderr.write("== 候選 %s：它對 HEAD %s 的差異 = %s 對它的基底 %s 的差異（%d 個檔，%s，每個檔的內容和 %s 相同）\n"
                     % (c[:7], head[:7], short, base[:7], len(paths),
                        "候選的 tree 就是審過的 tree" if base == head else "patch-id 相同", short))
    sys.stderr.write("   檢查全部在暫時的 worktree 裡跑：%s；正式樹到換過去之前不動\n" % cand)
    print("CAND=%s\nC=%s\nHEAD0=%s" % (_sh(cand), c, head))
    return 0


def _sh(word):
    return "'" + word.replace("'", "'\"'\"'") + "'"


def state():
    try:
        with open(os.path.join(keep_dir(), "state.json"), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        raise Stop("沒有候選的紀錄（%s/state.json）" % keep_dir())


def land(candidate, head):
    """Exit 0: the branch is at the candidate. 3: HEAD moved since the candidate was made (make a new one).
    1: refused, nothing changed. `candidate` and `head` are what this deploy.sh was given by prepare and ran its
    checks on: another deploy that made a candidate of its own meanwhile is not landed in its place."""
    s = state()
    if (s["candidate"], s["head"]) != (candidate, head):
        raise Stop("候選已經不是這次檢查過的那個（另一個部署同時在跑？）：沒有換，正式樹不動")
    if git("rev-parse", "HEAD").stdout.strip() != s["head"]:
        sys.stderr.write("main 在檢查的這段時間裡多了別的 commit：重新做候選\n")
        return 3
    dirty = git("status", "--porcelain", "--", *s["paths"]).stdout.rstrip("\n")
    if dirty:
        raise Stop("檢查的這段時間裡，正式樹裡要換的檔被改了，不覆蓋：\n%s" % dirty)
    newer = behind(landing=True)  # again, under the lock: what came in while the checks ran
    if newer:
        raise Stop(NEWER % newer)
    git("merge", "--ff-only", "-q", s["candidate"])
    return 0


def untouched():
    """After a failed deploy: did it leave the live tree alone? The files it was to change are byte for byte as
    before and the candidate is no ancestor of the branch. This folder is many channels' work tree and the sync commits
    here: whatever else changed meanwhile is theirs, said in a line of its own, never taken for the deploy's."""
    s = state()
    now = snapshot(s["paths"])
    landed = git("merge-base", "--is-ancestor", s["candidate"], "HEAD", check=False).returncode == 0  # also under a
    if now["files"] == s["before"]["files"] and not landed:                 # commit the sync made on top of it
        print("== 正式樹沒有被這次部署動過：要換的 %d 個檔的內容和部署前一樣，候選也不在 main 的歷史裡" % len(s["paths"]))
        other = []
        if now["head"] != s["before"]["head"]:
            other.append("main 從 %s 到了 %s：%s" % (s["before"]["head"][:7], now["head"][:7], "；".join(
                git("log", "--format=%h %s", "%s..%s" % (s["before"]["head"], now["head"]), check=False).stdout.strip().split("\n")[:5])))
        was, is_now = set(s["before"]["status"].splitlines()), set(now["status"].splitlines())
        if was != is_now:
            other.append("沒提交的修改有變：%s" % "、".join(sorted(l.strip() for l in was ^ is_now)[:10]))
        if other:
            print("   部署期間別處有變動（別的頻道或同步做的，不是這次部署）：%s" % "；".join(other))
        print("   候選和測試輸出留在 %s（下次部署時清掉）" % keep_dir())
        return 0
    what = [w for w, bad in (("要換的檔的內容", now["files"] != s["before"]["files"]), ("候選已經在 main 的歷史裡", landed)) if bad]
    print("✗✗✗ 部署失敗，而且正式樹和部署前不一樣：%s。請馬上看 git status 和 git log" % "、".join(what))
    return 1


def touched(extra):
    """Paths (from the repo's root) with uncommitted changes under dcbot/ and the listed ones: what the in-place
    way would commit. Both names of a rename."""
    out = git("status", "--porcelain", "-z", "--", "dcbot", *extra).stdout.split("\0")
    paths, i = [], 0
    while i < len(out):
        entry = out[i]
        i += 1
        if len(entry) < 4:
            continue
        paths.append(entry[3:])
        if entry[0] in "RC" or entry[1] in "RC":  # followed by the name it had
            if i < len(out) and out[i]:
                paths.append(out[i])
            i += 1
    return paths


def main(argv):
    try:
        if argv[:1] == ["prepare"] and len(argv) >= 3:
            return prepare(argv[1], argv[2:])
        if argv[:1] == ["land"] and len(argv) == 3:
            return land(argv[1], argv[2])
        if argv == ["untouched"]:
            return untouched()
        if argv == ["done"]:
            forget_candidate()
            return 0
        if argv[:1] == ["in-place"]:
            live = live_modules()
            for p in touched(argv[1:]):
                if not in_place_ok(p, live):
                    print(p)
            return 0
    except Stop as e:
        sys.stderr.write("✗ %s\n" % e)
        return 1
    sys.stderr.write(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
