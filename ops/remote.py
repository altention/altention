#!/usr/bin/env python3
"""Where this installation's private data is backed up, and that it never goes anywhere public (C-25).

  python3 ops/remote.py check URL [--for NAME]   the checks below, nothing written; exit 0 when they all pass
  python3 ops/remote.py set NAME URL [--replace] check, then write it down, give the repo its origin, put the
                                                 pre-push hook in and push once; --replace: an origin or setting
                                                 that says another URL is changed (else it is left and said)
  python3 ops/remote.py status [--json]          each one: its folder, its remote, the hook, commits not pushed, the
                                                 last push seen, what the last check said
  python3 ops/remote.py hook NAME                put in (or refresh) the pre-push hook of that repo
  python3 ops/remote.py pre-push REMOTE URL      what the hook runs: exit 10 (no push) when anyone can read URL
                                                 or it is the engine's; any other failure lets the push go

NAME: instance (the instance repo itself), logs (chatlog's, sync.dirs.chatlog.remote), progress
(sync.dirs.progress.remote), planner (the secretary's folder, sync.dirs.planner.remote).

The checks (all must pass before anything is written):
- no user, password or token in an https (or any but ssh://) URL: GitHub takes a token as the user alone, and
  the URL is written into the settings and goes into git;
- not the engine: not the URL of any of the engine's remotes (the engine is public);
- nobody reads it anonymously: `git ls-remote` with no credentials at all (an empty HOME, no helper, no prompt),
  the URL turned into https first (git@host:o/r and ssh:// too); on github.com also the API without a token. Read
  that way: public, refused. Asked for a login or not found: private. Not reachable that way (offline, a host
  only reached over SSH): cannot tell, which is no refusal (owner 2026-10-16: only what is known to be public is
  stopped; doctor says it could not be told);
- empty, or holding this kind of data already (an instance has plugins.json or machines.json at its top ...), and
  never an engine's tree: data is not pushed into some other repo;
- reachable the way cron reaches it (a bare environment, ssh in BatchMode), so the 15-minute pushes go through.
The pre-push hook checks the second one again before every push, whoever pushes (the sync, chatlog, progress, the
secretary, a person); its answer is kept an hour, except "public", which is asked again every time. A push it
stops is written to the alerts (doctor shows it). Known limit (manager 2026-10-16): a repo made public after a
"private" or "cannot tell" answer is still pushed to for up to that hour.

  python3 ops/remote.py attach NAME [URL FOLDER]   what the plugins run on a repo already there (chatlog's install, progress, the
                                      secretary's folder): its setting given as origin when it has none (checked
                                      first), the hook put in; an origin that says another URL is left and said
  python3 ops/remote.py clone NAME URL FOLDER     what they run on a new machine before that: URL cloned into FOLDER
                                      only when it passes the checks (else nothing cloned, said in the alerts);
                                      a remote whose HEAD names no branch: its main, or its only branch, checked
                                      out (C-43); several and no main, or none: said, nothing guessed
Standard library, Python 3.8.
"""
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ENGINE = os.path.dirname(HERE)
sys.path.insert(0, ENGINE)
from altention import config  # noqa: E402

NAMES = {"instance": None, "logs": "chatlog", "progress": "progress", "planner": "planner"}  # -> sync.dirs.<key>
LABELS = {"instance": "實例", "logs": "對話紀錄", "progress": "進度", "planner": "秘書"}
ON_MAIN = ("logs", "progress")  # their plugins put the clone on main (`checkout -B main`) and push it there
HOOK_MARK = "# altention pre-push (ops/remote.py)"
KEEP = 3600            # seconds a check's answer is kept for the hook (never "public": asked again every time)
NET_TIMEOUT = 30
GITHUB_API = "https://api.github.com"
# git's words (LC_ALL=C) for "this needs a login" or "no such repo for you": private as far as anyone can tell
PRIVATE_WORDS = re.compile(r"could not read Username|could not read Password|terminal prompts disabled|Authentication "
                           r"failed|Repository not found|repository '.*' not found|not found|HTTP 40[134]|returned error: "
                           r"40[134]|Permission denied|access denied|denied to", re.I)


def state_dir():
    return os.path.expanduser("~/.local/state/altention")


def checks_path():
    return os.path.join(state_dir(), "remote-checks.json")


# what git sets for the repo at hand (`git rev-parse --local-env-vars`): a hook gets GIT_DIR from a push made in a
# linked worktree or with --git-dir, and every git command here would read that repo instead of the one named
# (the data repo taken for the engine, its push stopped: review of cbc6d25)
GIT_LOCAL_ENV = ("GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_CONFIG", "GIT_CONFIG_PARAMETERS", "GIT_CONFIG_COUNT",
                 "GIT_OBJECT_DIRECTORY", "GIT_DIR", "GIT_WORK_TREE", "GIT_IMPLICIT_WORK_TREE", "GIT_GRAFT_FILE",
                 "GIT_INDEX_FILE", "GIT_NO_REPLACE_OBJECTS", "GIT_REPLACE_REF_BASE", "GIT_PREFIX", "GIT_SHALLOW_FILE",
                 "GIT_COMMON_DIR")
STOP = 10  # pre-push's exit status for a push it stops; any other failure of this file lets the push go (the hook)


def run(cmd, env=None, timeout=NET_TIMEOUT, cwd=None):
    env = dict(os.environ if env is None else env)
    for name in GIT_LOCAL_ENV:
        env.pop(name, None)
    try:
        return subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True,
                              env=env, timeout=timeout, cwd=cwd, stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(cmd, 124, "", "timeout after %ds" % timeout)
    except OSError as e:
        return subprocess.CompletedProcess(cmd, 127, "", str(e))


BOILERPLATE = ("Please make sure you have the correct access rights", "and the repository exists.")


def said(r):
    """What git said, in one line that names the cause: every line but git's two of boilerplate after an SSH
    failure (once only the last one was kept: "and the repository exists."), or the exit status."""
    lines = [l.strip() for l in (r.stderr or "").splitlines() if l.strip() and l.strip() not in BOILERPLATE]
    return " / ".join(lines)[:240] if lines else "rc=%d" % r.returncode


def git(path, *args, timeout=NET_TIMEOUT):
    return run(["git", "-C", path] + list(args), timeout=timeout)


# ---------------------------------------------------------------- what a URL is

def is_local(url):
    return url.startswith(("/", "~", "./", "../", "file://")) or (os.sep in url and ":" not in url)


def parts(url):
    """(host, path) of a URL, lower case, without user, port, ".git" or slashes at the ends; a local path: ("",
    its real path). Two spellings of one repo come out the same."""
    url = url.strip()
    if is_local(url):
        path = url[len("file://"):] if url.startswith("file://") else url
        return "", os.path.realpath(os.path.expanduser(path)).rstrip("/")
    m = re.match(r"^[a-z][a-z0-9+.-]*://(?:[^@/]*@)?([^/:]+)(?::\d*)?(/.*)?$", url, re.I)
    if not m:
        m = re.match(r"^(?:[^@/]+@)?([^/:]+):(.*)$", url)  # scp-like: git@host:owner/repo
    if not m:
        return "", url
    host, path = m.group(1).lower(), (m.group(2) or "").strip("/")
    if path.endswith(".git"):
        path = path[:-4]
    return host, path.strip("/").lower()


def same_repo(a, b):
    return parts(a) == parts(b)


def anonymous_form(url):
    """(the URL as anyone would read it, how it was made): https for ssh and scp-like ones, no user or password
    in it; ("", "local") for a path on this machine."""
    if is_local(url):
        return "", "local"
    m = re.match(r"^(https?|git)://(?:[^@/]*@)?(.*)$", url, re.I)
    if m:
        return "%s://%s" % (m.group(1).lower(), m.group(2)), "as-is"
    host, path = parts(url)
    m = re.match(r"^(?:[^@/]+@)?([^/:]+):(.*)$", url) if "://" not in url else None
    raw = (m.group(2) if m else re.sub(r"^[a-z+]+://(?:[^@/]*@)?[^/]+/", "", url, flags=re.I)).strip("/")
    return "https://%s/%s" % (host, raw), "from-ssh"


# ---------------------------------------------------------------- the checks

def anonymous(url):
    """("public" | "private" | "unknown", why): can anyone at all read this repo?"""
    target, how = anonymous_form(url)
    if how == "local":
        return "private", "這台機器上的路徑"
    home = tempfile.mkdtemp(prefix="alt-anon-")
    try:
        env = {"PATH": os.environ.get("PATH") or "/usr/bin:/bin", "HOME": home, "GIT_TERMINAL_PROMPT": "0",
               "GIT_ASKPASS": "/bin/false", "SSH_ASKPASS": "/bin/false", "GIT_CONFIG_NOSYSTEM": "1",
               "GIT_CONFIG_GLOBAL": os.devnull, "LC_ALL": "C", "LANG": "C",
               "GIT_CEILING_DIRECTORIES": os.path.dirname(home)}
        # run in the empty HOME, never in the repo at hand: its own .git/config (url.*.insteadOf, http.extraheader)
        # would lend this read a login (the hook runs inside the data repo)
        r = run(["git", "-c", "credential.helper=", "ls-remote", "--", target], env=env, cwd=home)
    finally:
        shutil.rmtree(home, ignore_errors=True)
    if r.returncode == 0:
        verdict, why = "public", "沒有任何帳號也讀得到 %s" % target
    elif PRIVATE_WORDS.search(r.stderr or ""):
        verdict, why = "private", "沒有帳號讀不到（%s）" % said(r)
    else:
        verdict, why = "unknown", "沒辦法確認：%s 連不上（%s）" % (target, said(r))
        if how == "from-ssh":
            why += "；只能用 SSH 連的主機，這一項判斷不了"
    host, path = parts(url)
    if host == "github.com" and path.count("/") == 1:
        api = github_public(path)
        if api == "public":
            return "public", "GitHub 說 %s 是公開的" % path
        if api == "private" and verdict == "unknown":
            return "private", "GitHub 不給沒有帳號的人看 %s" % path
    return verdict, why


def github_public(owner_repo):
    """"public" (200), "private" (404: not there or not for everyone), "" when GitHub could not be asked."""
    try:
        req = urllib.request.Request("%s/repos/%s" % (GITHUB_API, owner_repo), headers={"Accept": "application/vnd.github+json",
                                                                       "User-Agent": "altention-remote-check"})
        with urllib.request.urlopen(req, timeout=15) as r:
            return "public" if r.status == 200 else ""
    except urllib.error.HTTPError as e:
        return "private" if e.code == 404 else ""
    except (OSError, ValueError):
        return ""


def engine_urls():
    r = git(ENGINE, "remote", "-v", timeout=20)
    # "<name>\t<url> (fetch)": cut at the tab and the last " (", a folder with a space in its name kept whole
    return sorted(set(line.split("\t", 1)[1].rsplit(" (", 1)[0] for line in r.stdout.splitlines() if "\t" in line))


def cron_env():
    return {"PATH": "/usr/bin:/bin:/usr/local/bin", "HOME": os.path.expanduser("~"), "LANG": "C", "LC_ALL": "C",
            "GIT_TERMINAL_PROMPT": "0", "GIT_SSH_COMMAND": "ssh -o BatchMode=yes -o ConnectTimeout=10"}


KINDS = {  # what a repo of each kind holds (any one of these paths): checked when it is not empty
    "instance": (re.compile(r"^(plugins\.json|machines\.json)$"), "plugins.json 或 machines.json"),
    "logs": (re.compile(r"(^|/)(meta\.json|raw\.jsonl|raw\.part-\d+\.jsonl)$"), "對話紀錄的 meta.json 或 raw.jsonl"),
    "progress": (re.compile(r"^(STATUS\.md|projects/)"), "STATUS.md 或 projects/"),
    "planner": (re.compile(r"^(plan\.md|reminders\.json|CLAUDE\.md)$"), "plan.md、reminders.json 或 CLAUDE.md"),
}
ENGINE_TREE = re.compile(r"^(altention/sync\.py|ops/doctor\.py)$")


def tree_of(url):
    """(file names at the tip of the remote's default branch, problem): [] for an empty repo; read with this
    user's own credentials, only the trees fetched (no file contents)."""
    r = run(["git", "ls-remote", "--symref", "--", url, "HEAD"], timeout=60)
    if r.returncode != 0:
        return None, "讀不到（%s）" % said(r)
    heads = run(["git", "ls-remote", "--heads", "--", url], timeout=60)
    if not r.stdout.strip() and not heads.stdout.strip():
        return [], None
    work = tempfile.mkdtemp(prefix="alt-tree-")
    try:
        run(["git", "init", "-q", "--bare", work])
        run(["git", "-C", work, "remote", "add", "src", url])  # named: a partial fetch needs a remote's name
        ref = "HEAD" if r.stdout.strip() else heads.stdout.split()[1]
        f = run(["git", "-C", work, "fetch", "-q", "--depth", "1", "--filter=blob:none", "src", ref], timeout=600)
        if f.returncode != 0:
            return None, "抓不到（%s）" % said(f)
        t = run(["git", "-C", work, "ls-tree", "-r", "--name-only", "FETCH_HEAD"], timeout=120)
        return t.stdout.splitlines(), None
    finally:
        shutil.rmtree(work, ignore_errors=True)


def check(url, name=None):
    """[(item, "ok" | "no" | "unsure", text)], in the order they are asked; a "no" stops the ones after it that
    need the network."""
    if re.match(r"^(?!ssh://)[a-z][a-z0-9+.-]*://[^/@]+@", url.strip(), re.I):  # https://<token>@host works too
        return [("secret", "no", "網址裡有帳號、密碼或 token：它會被寫進設定、進 git。用不帶帳號的網址（https://主機/…），"
                 "登入交給 gh auth setup-git 或 git 的 credential helper")]
    out = []
    eng = [u for u in engine_urls() if same_repo(u, url)]
    out.append(("engine", "no" if eng else "ok", "這是引擎的 repo（%s），引擎是公開的：資料不能放這裡" % eng[0] if eng
                else "不是引擎的 repo"))
    if eng:
        return out
    verdict, why = anonymous(url)
    out.append(("anonymous", {"public": "no", "private": "ok", "unknown": "unsure"}[verdict],
                ("這個 repo 是公開的（%s）：改成 private 再設，或換一個 private 的" % why) if verdict == "public" else why))
    remember(url, verdict, why)
    if verdict == "public":
        return out
    files, problem = tree_of(url)
    if files is None:
        out.append(("content", "no", "用這台的帳號%s：網址打錯，或這個帳號還沒有權限" % problem))
        return out
    if any(ENGINE_TREE.match(f) for f in files):
        out.append(("content", "no", "裡面是一份引擎（有 altention/sync.py）：資料不能放進引擎的 repo"))
    elif not files:
        out.append(("content", "ok", "是空的"))
    elif name in KINDS:
        pattern, words = KINDS[name]
        out.append(("content", "ok", "已經有%s的資料" % LABELS[name]) if any(pattern.search(f) for f in files) else
                   ("content", "no", "不是空的，也看不出是%s的資料（沒有 %s）：免得推進別的 repo，換一個空的" % (LABELS[name], words)))
    else:
        out.append(("content", "unsure", "不是空的（沒說要放哪一樣，沒有核對內容）"))
    if out[-1][1] == "no":
        return out
    r = run(["git", "ls-remote", "--", url], env=cron_env(), timeout=60)
    out.append(("cron", "ok", "排程的環境也連得到") if r.returncode == 0 else
               ("cron", "no", "排程（cron）的環境下連不上：金鑰可能有密碼、只存在 ssh-agent 裡，或用的是只在終端機裡有的登入"
                "（%s）。HTTPS 的話跑一次 gh auth setup-git；SSH 的話用沒有密碼的金鑰"
                % said(r)))
    return out


def passed(results):
    return all(s != "no" for _, s, _ in results)


# ---------------------------------------------------------------- the hook's memory

def read_checks():
    try:
        with open(checks_path(), encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def key_of(url):
    host, path = parts(url)
    return "%s/%s" % (host, path)


def remember(url, verdict, why):
    d = read_checks()
    d[key_of(url)] = {"at": time.time(), "verdict": verdict, "why": why, "url": url}
    try:
        os.makedirs(state_dir(), exist_ok=True)
        tmp = checks_path() + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=1)
        os.replace(tmp, checks_path())
    except OSError:
        pass


def recent(url):
    """The last answer for this URL when it is under KEEP old and not "public", else None."""
    e = read_checks().get(key_of(url))
    if not isinstance(e, dict) or e.get("verdict") not in ("private", "unknown"):
        return None
    at = e.get("at")
    if not isinstance(at, (int, float)) or isinstance(at, bool) or not 0 <= time.time() - at < KEEP:
        return None
    return e["verdict"], e.get("why") or ""


def alert(kind, text):
    try:
        from altention import update
        update.alert("remote", kind, text)
    except Exception:
        pass


def pre_push(remote, url):
    """0: let the push go; STOP: stopped (anyone can read it, or it is the engine's repo)."""
    eng = [u for u in engine_urls() if same_repo(u, url)]
    if eng:  # the hook lives only in the data repos: one of them pointed at the engine's repo
        say = "沒有推：%s（%s）是引擎的 repo，引擎是公開的" % (remote, url)
        print("altention：" + say, file=sys.stderr)
        alert("engine-url", "%s：%s" % (pushing_repo(), say))
        return STOP
    kept = recent(url)
    verdict, why = kept if kept else anonymous(url)
    if not kept:
        remember(url, verdict, why)
    if verdict == "public":
        say = "沒有推：%s（%s）沒有帳號也讀得到，是公開的 repo。改成 private 之後就會照常推" % (remote, url)
        print("altention：" + say, file=sys.stderr)
        alert("public", "%s：%s" % (pushing_repo(), say))
        return STOP
    return 0


def pushing_repo():
    """The repo being pushed, for the alerts: read with the variables git gave the hook (GIT_DIR=<abs> git push
    from /tmp: its work tree, not the folder the push was typed in; review of d1d71c8)."""
    try:
        r = subprocess.run(["git", "rev-parse", "--absolute-git-dir"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                           universal_newlines=True, timeout=20, stdin=subprocess.DEVNULL)
        d = r.stdout.strip() if r.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        d = ""
    return repo_of_git_dir(d, os.environ.get("GIT_WORK_TREE")) or os.getcwd()


def repo_of_git_dir(d, work_tree=None):
    """<repo>/.git -> <repo>; <repo>/.git/worktrees/<name> -> that worktree (its gitdir file); else the git dir."""
    if work_tree:
        return os.path.abspath(work_tree)
    if not d:
        return ""
    if os.path.basename(d) == ".git":
        return os.path.dirname(d)
    try:
        with open(os.path.join(d, "gitdir"), encoding="utf-8") as f:
            linked = f.read().strip()
        if os.path.basename(linked) == ".git":
            return os.path.dirname(linked)
    except OSError:
        pass
    return d


# ---------------------------------------------------------------- the four folders

def folder_of(name):
    """Where that one lives on this machine (it may not be there yet); None for the instance when there is none."""
    if name == "instance":
        return config.instance_dir()
    data = os.environ.get("ALT_DATA") or os.path.join(config.root(), "data")
    if name == "logs":
        return os.path.expanduser(os.environ.get("CHATLOG_REPO") or os.path.join(data, "logs"))
    if name == "progress":
        return os.path.join(os.path.expanduser(os.environ.get("PROGRESS_WORK") or data), "progress")
    value = config.setting("planner.folder")
    if isinstance(value, str) and value.strip():
        return os.path.expanduser(value.strip())
    return os.path.join(os.path.expanduser(data), "planner")


def is_repo(path):
    return bool(path) and os.path.isdir(path) and git(path, "rev-parse", "--git-dir", timeout=20).returncode == 0


def origin(path):
    r = git(path, "remote", "get-url", "origin", timeout=20)
    return r.stdout.strip() if r.returncode == 0 else ""


def hook_path(path):
    r = git(path, "rev-parse", "--git-path", "hooks", timeout=20)
    hooks = r.stdout.strip()
    return os.path.join(hooks if os.path.isabs(hooks) else os.path.join(path, hooks), "pre-push")


def hook_text():
    """The hook: stops a push only when remote.py says so (STOP); remote.py gone or failing (it cannot be
    imported, it raised) lets the push go, said on stderr and in the alerts (manager 2026-10-16: a fault here never
    stops the owner's backups)."""
    return ("#!/bin/sh\n%s: never push to a repo anyone can read (C-25)\n"
            "r=%s\n"
            "told() {\n"
            "  echo \"altention pre-push: $1, this push was not checked\" >&2\n"
            "  a=\"$HOME/.local/state/altention\"\n"
            "  d=$(git rev-parse --absolute-git-dir 2>/dev/null)\n"
            "  if [ -n \"$GIT_WORK_TREE\" ]; then d=\"$GIT_WORK_TREE\"\n"
            "  elif [ \"${d##*/}\" = .git ]; then d=\"${d%%/.git}\"\n"
            "  elif [ -f \"$d/gitdir\" ]; then d=$(dirname \"$(cat \"$d/gitdir\")\")\n"
            "  fi\n"
            "  mkdir -p \"$a\" 2>/dev/null && printf '%%s\\tremote\\thook-broken\\t%%s\\n' \"$(date +%%Y-%%m-%%dT%%H:%%M:%%S%%z)\" \\\n"
            "    \"${d:-$(pwd)}：pre-push 沒有檢查就放行（$1）\" >> \"$a/alerts\" 2>/dev/null\n"
            "  exit 0\n"
            "}\n"
            "[ -f \"$r\" ] || told \"$r is gone\"\n"
            "python3 \"$r\" pre-push \"$1\" \"$2\"\n"
            "rc=$?\n"
            "[ \"$rc\" = 0 ] && exit 0\n"
            "[ \"$rc\" = %d ] && exit 1\n"
            "told \"$r failed (exit $rc)\"\n" % (HOOK_MARK, shlex.quote(os.path.join(ENGINE, "ops", "remote.py")), STOP))


def own_hooks(path):
    """This repo's own hooks folder (<common git dir>/hooks), whatever core.hooksPath says."""
    r = git(path, "rev-parse", "--git-common-dir", timeout=20)
    d = r.stdout.strip()
    return os.path.realpath(os.path.join(d if os.path.isabs(d) else os.path.join(path, d), "hooks"))


def put_hook(path):
    """(ok, text): our pre-push hook in this repo; another one already there is left as it is and said. Hooks
    taken from elsewhere (core.hooksPath: often a folder every repo of this user shares, where ours would check
    every push of every repo, the engine's own among them; or the repo's own, such as husky's) get nothing."""
    hp = hook_path(path)
    if os.path.realpath(os.path.dirname(hp)) != own_hooks(path):
        return False, ("%s 的 hook 來自 core.hooksPath（%s），沒有放進去：推送前不會檢查是不是公開的"
                       % (path, os.path.dirname(hp)))
    try:
        with open(hp, encoding="utf-8", errors="replace") as f:
            there = f.read()
    except OSError:
        there = None
    if there is not None and HOOK_MARK not in there:
        return False, "%s 已經有別的 pre-push hook（%s），沒有動它：推送前不會檢查是不是公開的" % (path, hp)
    if there == hook_text():
        return True, "pre-push hook 已經在"
    os.makedirs(os.path.dirname(hp), exist_ok=True)
    tmp = hp + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(hook_text())
    os.chmod(tmp, 0o755)
    os.replace(tmp, hp)
    return True, "放好 pre-push hook"


def has_hook(path):
    try:
        with open(hook_path(path), encoding="utf-8", errors="replace") as f:
            return HOOK_MARK in f.read()
    except OSError:
        return False


def write_setting(key, url):
    """sync.dirs.<key>.remote = url in the instance's plugins.json, every other byte of meaning kept."""
    path = os.path.join(config.instance_dir(), "plugins.json")
    with open(path, encoding="utf-8") as f:
        d = json.load(f)
    if not isinstance(d, dict):
        raise ValueError("plugins.json 不是一個物件")
    node = d.setdefault("sync", {})
    node = node.setdefault("dirs", {}) if isinstance(node, dict) else None
    node = node.setdefault(key, {}) if isinstance(node, dict) else None
    if not isinstance(node, dict):
        raise ValueError("plugins.json 的 sync.dirs.%s 不是物件" % key)
    node["remote"] = url
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(json.dumps(d, ensure_ascii=False, indent=2) + "\n")
    os.replace(tmp, path)


def set_remote(name, url, replace=False, say=print):
    """0 when it is set (and pushed, if there was anything to push); 1 when a check or a step stopped it."""
    results = check(url, name)
    for item, status, text in results:
        say("  %s %s" % ({"ok": "✓", "no": "✗", "unsure": "?"}[status], text))
    if not passed(results):
        say("✗ 沒有設定%s的遠端" % LABELS[name])
        return 1
    if name != "instance":
        if not config.instance_dir():
            say("✗ 這台還沒有實例，%s的遠端沒地方寫" % LABELS[name])
            return 1
        before = config.setting("sync.dirs.%s.remote" % NAMES[name])
        if before and not same_repo(before, url) and not replace:
            say("✗ 實例的 sync.dirs.%s.remote 已經是 %s，沒有改；要換成 %s 加 --replace" % (NAMES[name], before, url))
            return 1
        if before != url:
            write_setting(NAMES[name], url)
            say("✓ 寫進實例 plugins.json 的 sync.dirs.%s.remote" % NAMES[name])
    path = folder_of(name)
    if not is_repo(path):
        say("· %s 還不是 git repo：%s" % (path, "安裝那一樣時會照設定接上" if name != "instance" else "先建實例"))
        return 0 if name != "instance" else 1
    from altention import sync
    try:
        with sync.locked(sync.Repo(path)):
            now = origin(path)
            if now and not same_repo(now, url) and not replace:
                say("✗ %s 的 origin 已經是 %s，沒有改；要換成 %s 加 --replace" % (path, now, url))
                return 1
            if not now:
                git(path, "remote", "add", "origin", url, timeout=20)
            elif now != url:
                git(path, "remote", "set-url", "origin", url, timeout=20)
            ok, text = put_hook(path)
            say(("✓ " if ok else "⚠ ") + text)
            if git(path, "rev-parse", "-q", "--verify", "HEAD", timeout=20).returncode != 0:
                say("· 還沒有任何 commit，之後的同步會推")
                return 0
            branch = git(path, "symbolic-ref", "--short", "HEAD", timeout=20).stdout.strip() or "main"
            r = git(path, "push", "-q", "-u", "origin", "HEAD:%s" % branch, timeout=600)
            if r.returncode != 0:
                say("✗ 第一次推送失敗：%s" % said(r))
                return 1
            git(path, "branch", "--set-upstream-to=origin/%s" % branch, branch, timeout=20)
            say("✓ 推上去了（%s → %s）" % (branch, url))
    except sync.Busy as e:
        say("✗ %s（別的同步還在跑），稍後再試" % e)
        return 1
    return 0


def attach(name, url=None, path=None):
    """(what, text) for a repo of `name` already on this machine, as the plugins find it (`url`, `path`: what the
    plugin itself worked out, its own environment overrides included; else the setting and folder_of):
    "set"      it had no origin; its setting (sync.dirs.<key>.remote) passed check() and is its origin now;
    "same"     its origin is the setting already (or, without a setting, whatever it has: left);
    "none"     no repo there, or neither an origin nor a setting: nothing to do;
    "differs"  its origin says another URL than the setting: left as it is, written to the alerts;
    "refused"  the setting did not pass check(): no origin added, written to the alerts.
    The pre-push hook is put in whenever the repo has an origin. No lock taken: a plugin may hold its own."""
    path = path or folder_of(name)
    if not is_repo(path):
        return "none", "%s 還不是 git repo" % path
    if url is None:
        url = (config.setting("sync.dirs.%s.remote" % NAMES[name]) or "") if NAMES.get(name) else ""
    now = origin(path)
    if not url and not now:
        return "none", "%s 沒有設定遠端：只在這台" % path
    if url and now and not same_repo(now, url):
        text = "%s 的 origin 是 %s，跟設定的 sync.dirs.%s.remote（%s）不一樣，沒有改；要換成設定的那個：python3 ops/remote.py " \
               "set %s %s --replace" % (path, now, NAMES[name], url, name, url)
        alert("origin-differs", text)
        return "differs", text
    what = "same"
    if url and not now:
        results = check(url, name)
        if not passed(results):
            text = "%s 沒有接上設定的遠端 %s：%s" % (path, url, "；".join(t for _i, st, t in results if st == "no"))
            alert("attach-refused", text)
            return "refused", text
        r = git(path, "remote", "add", "origin", url, timeout=20)
        if r.returncode != 0:
            return "refused", "%s 加不上 origin：%s" % (path, said(r))
        what = "set"
    ok, hook = put_hook(path)
    return what, ("接上 %s；" % url if what == "set" else "") + hook


def clone(name, url, path):
    """(ok, text): url cloned into path (missing or empty) only when check() passes it, the way attach() gives an
    origin (review of d1d71c8: a setting pointing at another data repo was cloned and given as origin unchecked);
    refused: nothing cloned, written to the alerts. No lock taken (attach)."""
    results = check(url, name)
    if not passed(results):
        text = "%s 沒有從設定的遠端 %s clone：%s" % (path, url, "；".join(t for _i, st, t in results if st == "no"))
        alert("clone-refused", text)
        return False, text
    r = run(["git", "clone", "-q", "--", url, path], timeout=1800)
    if r.returncode != 0:
        return False, "%s 沒有從 %s clone 回來：%s" % (path, url, said(r))
    branch, have = checkout_backup(path)
    if branch and branch != "main" and name in ON_MAIN:
        return True, "從 %s clone 回來（遠端原本的分支是 %s，之後推到 main）" % (url, branch)
    if branch:
        return True, "從 %s clone 回來（%s）" % (url, branch)
    if have:
        return True, "從 %s clone 了，但認不出哪個分支是備份（有 %s，沒有 main）：沒有 checkout，遠端的分支沒動" % (url, "、".join(have))
    return True, "從 %s clone 了，但裡面沒有內容（空的 repo）" % url
def checkout_backup(path):
    """(the branch a fresh clone is on with the backup's commits, or None; the remote's branches). A remote whose
    HEAD names a branch it does not have (a bare repo made by `git init --bare` says master; the data is on main:
    #code-review of bc09467, C-43) leaves the clone on an empty branch: then its main, or its only branch, is checked
    out tracking origin; several and no main: None (said, not guessed). Run again on that clone: the same answer."""
    def git_(*args):
        return git(path, *args, timeout=60)
    if git_("rev-parse", "-q", "--verify", "HEAD").returncode == 0:
        return git_("symbolic-ref", "--short", "-q", "HEAD").stdout.strip() or "HEAD", []
    have = remote_branches(path)
    pick = "main" if "main" in have else (have[0] if len(have) == 1 else None)
    if pick is None or git_("checkout", "-q", "-B", pick, "--track", "origin/%s" % pick).returncode != 0:
        return None, have
    return pick, have


def last_push(path):
    """When the upstream's ref here last moved (a push or a fetch that saw ours), or None."""
    r = git(path, "log", "-g", "-1", "--format=%ct", "@{u}", timeout=20)
    s = r.stdout.strip()
    return int(s) if r.returncode == 0 and s.isdigit() else None


def status():
    rows = []
    for name in NAMES:
        try:
            path = folder_of(name)
        except Exception as e:
            rows.append({"name": name, "error": "%s: %s" % (type(e).__name__, e)})
            continue
        row = {"name": name, "folder": path, "repo": is_repo(path)}
        if name != "instance":
            try:
                row["setting"] = config.setting("sync.dirs.%s.remote" % NAMES[name]) or ""
            except Exception:
                row["setting"] = ""
        if row["repo"]:
            row["origin"] = origin(path)
            row["hook"] = has_hook(path)
            ahead = git(path, "rev-list", "--count", "@{u}..HEAD", timeout=20)
            row["ahead"] = int(ahead.stdout.strip()) if ahead.returncode == 0 and ahead.stdout.strip().isdigit() else None
            row["last_push"] = last_push(path)
            row["branch"] = git(path, "symbolic-ref", "--short", "-q", "HEAD", timeout=20).stdout.strip()
            row["remote_branches"] = remote_branches(path)
            e = read_checks().get(key_of(row["origin"])) if row["origin"] else None
            row["check"] = {k: e.get(k) for k in ("verdict", "why", "at")} if isinstance(e, dict) else None
        rows.append(row)
    return rows


def remote_branches(path):
    """The origin's branches as this clone last saw them (refs/remotes/origin, its HEAD left out)."""
    out = git(path, "for-each-ref", "--format=%(refname:short)", "refs/remotes/origin", timeout=20).stdout.split()
    return [b[len("origin/"):] for b in out if b.startswith("origin/") and b != "origin/HEAD"]


def other_branches(row):
    """C-43: a backup cloned from a remote whose only branch was not main, by a plugin that keeps it on main: the
    branches it had, said beside the main pushed since, so that the second branch there is not a riddle."""
    if row.get("name") not in ON_MAIN or row.get("branch") != "main":
        return []
    return [b for b in row.get("remote_branches") or [] if b != "main"]


def show(rows):
    for row in rows:
        label = LABELS[row["name"]]
        if row.get("error"):
            print("%s：讀不出來（%s）" % (label, row["error"]))
            continue
        if not row["repo"]:
            print("%s：%s 還不是 git repo%s" % (label, row["folder"], "（設定的遠端：%s）" % row["setting"] if row.get("setting") else ""))
            continue
        if not row["origin"]:
            print("%s：%s 沒有遠端：只在這台，電腦壞了救不回來" % (label, row["folder"]))
            continue
        bits = ["遠端 %s" % row["origin"], "hook %s" % ("在" if row["hook"] else "不在")]
        if row["ahead"] is not None:
            bits.append("%d 個 commit 還沒推" % row["ahead"])
        if row["last_push"]:
            bits.append("上次推送 %s" % time.strftime("%Y-%m-%d %H:%M", time.localtime(row["last_push"])))
        if other_branches(row):
            bits.append("遠端原本的分支是 %s，之後推到 main" % "、".join(other_branches(row)))
        if row["check"]:
            bits.append({"public": "⚠ 公開的", "private": "private", "unknown": "沒辦法確認是不是公開的"}.get(
                row["check"].get("verdict"), "?"))
        print("%s：%s" % (label, "；".join(bits)))


def main(argv):
    if argv[:1] in (["set"], ["hook"], ["attach"], ["clone"]):  # these write this machine's repos and the instance's settings
        from altention import engine_id
        why = engine_id.refusal(ENGINE, "remote.py %s" % argv[0])
        if why:
            print(why)
            return 1
    if len(argv) >= 2 and argv[0] == "check":
        name = argv[argv.index("--for") + 1] if "--for" in argv[:-1] else None
        if name is not None and name not in NAMES:
            print("不認得 %s（可以是 %s）" % (name, "、".join(NAMES)))
            return 2
        results = check(argv[1], name)
        for _item, s, text in results:
            print("%s %s" % ({"ok": "✓", "no": "✗", "unsure": "?"}[s], text))
        return 0 if passed(results) else 1
    if len(argv) >= 3 and argv[0] == "set" and argv[1] in NAMES:
        return set_remote(argv[1], argv[2], replace="--replace" in argv[3:])
    if argv[:1] == ["status"]:
        rows = status()
        if "--json" in argv:
            print(json.dumps(rows, ensure_ascii=False, indent=1))
        else:
            show(rows)
        return 0
    if len(argv) == 2 and argv[0] == "hook" and argv[1] in NAMES:
        path = folder_of(argv[1])
        if not is_repo(path):
            print("%s 不是 git repo" % path)
            return 1
        ok, text = put_hook(path)
        print(text)
        return 0 if ok else 1
    if len(argv) in (2, 4) and argv[0] == "attach" and argv[1] in NAMES:  # attach NAME [URL PATH]
        what, text = attach(argv[1], *(argv[2:4] or [None, None]))
        print(("✓ " if what in ("set", "same", "none") else "⚠ ") + text)
        return 0 if what in ("set", "same", "none") else 1
    if len(argv) == 4 and argv[0] == "clone" and argv[1] in NAMES:  # clone NAME URL PATH
        ok, text = clone(*argv[1:4])
        print(("✓ " if ok else "⚠ ") + text)
        return 0 if ok else 1
    if len(argv) == 3 and argv[0] == "pre-push":
        return pre_push(argv[1], argv[2])
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
