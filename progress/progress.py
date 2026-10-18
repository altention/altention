#!/usr/bin/env python3
"""progress: the agent that keeps each project's progress.

Reads every session that chatlog mirrored into the logs repo, files its progress under the right
project in the progress repo (append-only logs), and regenerates STATUS.md files with the latest state.

  progress.py run [--dry-run] [--max N]   # cron runs this every 10 minutes
  progress.py status                      # last run, cost, pending sessions

Only this process writes to the progress repo. Sessions never write there directly; an explicit report
(/report) is just a marked message in the conversation, which reaches us through the logs repo.
Compatible with Python 3.8+.
"""
import argparse
import fcntl
import glob
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "chatlog"))
sys.path.insert(0, os.path.join(HERE, ".."))
import chatlog as cl  # noqa: E402  (reuses transcript parsing helpers)
from altention import background, config  # noqa: E402  (the instance, the machines)

DATA = os.environ.get("ALT_DATA") or os.path.join(os.environ.get("ALT_ROOT") or "~/altention", "data")  # {data} in plugin.json
WORK = os.path.expanduser(os.environ.get("PROGRESS_WORK") or DATA)
LOGS = os.path.join(WORK, "logs-mirror")         # read-only mirror, reset to origin every run
PROGRESS = os.path.join(WORK, "progress")
CLAUDE = os.environ.get("PROGRESS_CLAUDE") or shutil.which("claude") or os.path.expanduser("~/.local/bin/claude")
MODEL = os.environ.get("PROGRESS_MODEL", "sonnet")
STATUS_MODEL = os.environ.get("PROGRESS_STATUS_MODEL", "sonnet")
IDLE_MIN = int(os.environ.get("PROGRESS_IDLE_MIN", "20"))
BOOTSTRAP_DAYS = int(os.environ.get("PROGRESS_BOOTSTRAP_DAYS", "14"))
MAX_PER_RUN = int(os.environ.get("PROGRESS_MAX_PER_RUN", "20"))  # LLM calls per run
STATE_DIR = os.path.expanduser("~/.cache/progress")
DIGEST_MAX = 60000
CHECKPOINT = int(os.environ.get("PROGRESS_CHECKPOINT", "20"))  # commit + push every N LLM calls
SEG_MAX = int(os.environ.get("PROGRESS_SEG_MAX", "40000"))  # digest chars per LLM call
MARKER = "<!-- progress-report -->"
MISC = "_misc"
# sessions started by programs (claude -p / Agent SDK), not by the user at a terminal
SKIP_ENTRYPOINTS = set(os.environ.get("PROGRESS_SKIP_ENTRYPOINTS", "sdk-cli,sdk-py,sdk-ts").split(","))
TZ = cl.TZ


# ---------------------------------------------------------------- where the repos are

# name in the instance's sync.dirs
REPOS = {"logs": "chatlog", "progress": "progress"}
_origins = {}


def origin_of(repo):
    """A checkout's origin URL, or None."""
    if repo not in _origins:
        try:
            r = subprocess.run(["git", "-C", repo, "remote", "get-url", "origin"], stdout=subprocess.PIPE,
                               stderr=subprocess.DEVNULL, universal_newlines=True, timeout=30)
            _origins[repo] = r.stdout.strip() if r.returncode == 0 and r.stdout.strip() else None
        except (OSError, subprocess.SubprocessError):
            _origins[repo] = None
    return _origins[repo]


def sync_setting(which, field):
    return config.setting("sync.dirs.%s.%s" % (REPOS[which], field)) or None


def remote_of(which):
    """Where the logs ("logs") or the progress repo ("progress") live: $PROGRESS_LOGS_REMOTE / $PROGRESS_REMOTE, else the
    instance's sync.dirs.<name>.remote. None: nowhere (local only)."""
    env = os.environ.get("PROGRESS_LOGS_REMOTE" if which == "logs" else "PROGRESS_REMOTE")
    if env:
        return env
    if config.instance_dir():
        return sync_setting(which, "remote")
    return None


def logs_web():
    """The address a log's path is appended to for a link ($PROGRESS_LOGS_WEB, else sync.dirs.<name>.web, else
    worked out for a host whose addresses are known), or "" for no links."""
    web = os.environ.get("PROGRESS_LOGS_WEB") or sync_setting("logs", "web")
    if not web:
        remote = norm_remote(remote_of("logs") or (origin_of(LOGS) if os.path.isdir(LOGS) else None))
        web = "https://%s/tree/main/" % remote if remote.startswith("github.com/") else ""
    return web if not web or web.endswith("/") else web + "/"


# ---------------------------------------------------------------- utilities

def log(msg):
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(os.path.join(STATE_DIR, "progress.log"), "a") as f:
        f.write("%s %s\n" % (datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S"), msg))


def git(repo, *args, **kw):
    return subprocess.run(["git", "-C", repo] + list(args), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          universal_newlines=True, timeout=kw.get("timeout", 300))


def read(path, default=""):
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except OSError:
        return default


def read_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def append(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(text)


def write_json(path, obj):
    write(path, json.dumps(obj, ensure_ascii=False, indent=2) + "\n")


def now_utc():
    return datetime.now(timezone.utc)


def slug(s):
    s = re.sub(r"[^a-z0-9]+", "-", (s or "").lower()).strip("-")
    return s[:40] or "project"


def clip(text, n):
    text = text or ""
    return text if len(text) <= n else text[:n] + " …(截斷)"


def bullets(items):
    return "\n".join("- " + str(x) for x in items if str(x).strip())


# ---------------------------------------------------------------- repos

PROGRESS_README = """# 專案進度

由「進度」agent 自動維護的專案進度紀錄。它每 10 分鐘讀一次%s，
把每個 session 的進展歸到對應的專案。

- [`STATUS.md`](STATUS.md)：所有專案的最新狀態（每次更新都會重新產生，舊版在 git 歷史裡）
- `projects/<專案>/STATUS.md`：單一專案的最新進展
- `projects/<專案>/log/<年-月>.md`：進度日誌，只追加不修改
- `projects/<專案>/project.json`：專案身分（git remote、各機器上的路徑、別名）。**歸類錯了就改這裡**，之後會照著做
- `sessions/<年-月>.jsonl`：每個 session 被歸到哪個專案、怎麼判斷的
- `prompts/`：提示詞和教訓（`lessons.md` 可以直接寫給它看的規則）
"""

SUMMARIZE_PROMPT = """你是使用者的「進度」agent。使用者同時在多台機器、多個專案上用 Claude Code 工作，你負責追蹤每個專案的進度。

你會收到：
1. 目前已知的專案清單（id、名稱、說明、最新狀態）
2. 一個 session 的基本資料（機器、工作目錄、git remote）與「規則判斷的專案」（若有）
3. 這個 session 自上次處理後的新內容（使用者訊息、Claude 回覆、工具呼叫的摘要）

你要做的：
- 判斷這段內容屬於哪個專案。規則判斷有結果時，除非內容明顯在做別的專案，否則照用。一段內容可以拆給多個專案。
- 「專案」是長期存在的東西：一個 codebase（通常對應一個 git repo 或工作目錄的頂層資料夾）、一門課、一條研究主線。
  **不要用當下這個任務來命名專案**，要用 repo／資料夾／課程的名字（例如工作目錄是 `.../game-solver`，專案就是 `game-solver`，不是「架構預覽網頁」）。
- 在專案目錄裡做的學習、提問、讀程式碼，也算這個專案的進度（例如「理解了 X 的設計」），不要因為沒有產出就歸到 `_misc`。
- 沒有對應的專案時：如果是會持續做的工作，建立新專案（簡短的英文 id，例如 `project-web`）；只有在家目錄、暫存目錄做的一次性問答、閒聊、查資料，才歸到 `_misc`。
- 為每個專案寫一筆進度紀錄：只寫**實際發生的事**（完成了什麼、正在做什麼、卡在哪、做了什麼決定、下一步）。不要寫空話，不要把「討論了 X」寫成「完成了 X」。
- 如果內容裡有使用者用 /report 寫的「進度匯報」（標記 `<!-- progress-report -->`），以它為準，它是 session 對自己工作的正式回報。
- 一律用繁體中文，簡潔。每個清單最多 5 項，每項一句話。沒有就給空陣列。
"""

STATUS_PROMPT = """你是使用者的「進度」agent，負責維護一個專案的「最新進展」。

你會收到：專案說明、上一版的狀態、以及最近的進度日誌（由新到舊）。請產生這個專案**現在**的狀態：
- state：active（最近有在做）、blocked（卡住，等某件事）、paused（一陣子沒動）、done（已完成）
- headline：一句話說明現在在做什麼、做到哪
- current / recent_done / blockers / decisions / next：各最多 5 項，每項一句話
- 以最新的日誌為準；已經完成或不再成立的阻礙、待辦要移除，不要無限累積
- 一律用繁體中文，簡潔，不寫空話
"""

LESSONS_SEED = """# 進度的教訓與規則

「進度」agent 每次工作前都會讀這份檔案。使用者可以直接在這裡寫規則，例如：
- 「solver 和 solver-web 是同一個專案」
- 「網站的維運工作歸在 website，不要另開專案」
"""


class NoLogsYet(Exception):
    """chatlog's folder here has no conversation in it yet (a new install): nothing to do this round."""


def logs_source():
    """Where the mirror of chatlog's records comes from: the logs remote when one is set, else chatlog's own folder on
    this machine (one machine, no remote: what a new install has; the owner's re-install, 2026-10-15). SystemExit,
    in plain words, when there is neither; NoLogsYet when that folder has no commit yet."""
    remote = remote_of("logs")
    if remote:
        return remote
    here = cl.REPO
    if not os.path.isdir(os.path.join(here, ".git")):
        raise SystemExit("這台沒有對話紀錄（%s 不是對話紀錄的資料夾），也沒有設對話紀錄的遠端 repo。進度要跟對話紀錄在同一台機器上；"
                         "不在同一台的話，在實例的 plugins.json 設 sync.dirs.chatlog.remote（對話紀錄 repo 的位址）" % here)
    if git(here, "rev-parse", "-q", "--verify", "main^{commit}").returncode != 0:
        raise NoLogsYet()
    return here


def private_dir(path):
    """Make path (if missing) and keep it 700: what was said in every conversation, and what was made of it. Made
    before anything is cloned or written into it, so no other account can read it even for a moment (a first round
    left both 755 once: the chmod ran only on folders already there, 2026-10-15)."""
    os.makedirs(path, mode=0o700, exist_ok=True)
    os.chmod(path, 0o700)


def altremote():
    sys.path.insert(0, os.path.join(HERE, "..", "ops"))
    import remote
    return remote


def clone_progress(remote):
    """True when the progress repo came back from its remote: cloned only when ops/remote.py checks it (never a
    public repo, the engine's or some other data's); a refusal goes into the log and the alerts, and the repo is
    made new here instead."""
    try:
        ok, text = altremote().clone("progress", remote, PROGRESS)
    except Exception as e:
        ok, text = False, "%s: %s" % (type(e).__name__, e)
    if not ok:
        log("remote: %s" % text)
    return ok


def attach_progress(remote):
    """The progress repo given its remote when it has none (checked first: never a public repo, the engine's or
    some other data's) and the pre-push hook (ops/remote.py attach, C-25); what it did goes into the log. A
    problem there stops nothing here: the repo is kept locally until it is fixed."""
    try:
        what, text = altremote().attach("progress", remote, PROGRESS)
    except Exception as e:
        what, text = "error", "%s: %s" % (type(e).__name__, e)
    _origins.pop(PROGRESS, None)
    if what != "same" and what != "none":
        log("remote: %s %s" % (what, text))


def ensure_repos():
    os.makedirs(WORK, exist_ok=True)
    source = logs_source()
    for private in (LOGS, PROGRESS):
        private_dir(private)
    if not os.path.isdir(os.path.join(LOGS, ".git")):
        subprocess.run(["git", "clone", "-q", source, LOGS], check=True, timeout=1800)  # into the empty 700 folder
    else:
        if origin_of(LOGS) != source:  # a remote set (or dropped) since the mirror was made
            git(LOGS, "remote", "set-url", "origin", source)
            _origins.pop(LOGS, None)
        git(LOGS, "fetch", "-q", "origin", timeout=900)
        git(LOGS, "reset", "-q", "--hard", "origin/main")
    if not os.path.isdir(os.path.join(PROGRESS, ".git")):
        remote = remote_of("progress")
        if not (remote and clone_progress(remote)):
            private_dir(PROGRESS)
            git(PROGRESS, "init", "-q")
        git(PROGRESS, "checkout", "-q", "-B", "main")
    attach_progress(remote_of("progress") or "")
    if origin_of(PROGRESS) and git(PROGRESS, "rev-parse", "-q", "--verify", "@{u}").returncode == 0:
        git(PROGRESS, "pull", "-q", "--rebase", "--autostash")
    web = logs_web()
    seeds = [("README.md", PROGRESS_README % (" [對話紀錄](%s)" % web.split("/tree/")[0].rstrip("/") if web else "對話紀錄")), ("prompts/summarize.md", SUMMARIZE_PROMPT),
             ("prompts/status.md", STATUS_PROMPT), ("prompts/lessons.md", LESSONS_SEED)]
    for rel, text in seeds:
        if not os.path.exists(os.path.join(PROGRESS, rel)):
            write(os.path.join(PROGRESS, rel), text)
    if not os.path.exists(os.path.join(PROGRESS, "STATUS.md")):
        write_root_status({}, "正在整理所有過去的對話，完成後這裡會列出每個專案的最新狀態。")
    misc = os.path.join(PROGRESS, "projects", MISC, "project.json")
    if not os.path.exists(misc):
        write_json(misc, {"id": MISC, "name": "雜項", "description": "一次性的問答、查資料、閒聊，不屬於任何持續進行的專案",
                          "remotes": [], "paths": [], "created": now_utc().isoformat(timespec="seconds")})


# ---------------------------------------------------------------- projects

def norm_remote(url):
    url = (url or "").strip()
    m = re.match(r"^(?:ssh://)?git@([^:/]+)[:/](.+?)(?:\.git)?/?$", url) or \
        re.match(r"^https?://(?:[^@/]+@)?([^/]+)/(.+?)(?:\.git)?/?$", url)
    return ("%s/%s" % (m.group(1).lower(), m.group(2))) if m else url


def home_dirs():
    """Home folders, here and on every machine of the list (sessions come from all of them), plus the
    instance's progress.home_dirs."""
    homes = [os.path.expanduser("~")] + [m.get("home") for m in config.machines().values()]
    homes += config.setting("progress.home_dirs") or []
    return set(h.rstrip("/") for h in homes if h and h.rstrip("/"))


def is_generic_dir(cwd):
    """A folder that says nothing about the project: a home folder, a temporary one."""
    if not cwd or cwd == "/":
        return True
    return bool(cwd.rstrip("/") in home_dirs()
                or re.match(r"^/(Users|home)/[^/]+/?$|^/(private/)?tmp(/|$)|^/var/folders/", cwd)
                or "scratch" in cwd or "Application Support/Claude" in cwd)


def instance_paths():
    """Where the instance is: here, at the usual place under every machine's home, and progress.instance_paths."""
    paths = [config.instance_dir()] + [os.path.join(h, "altention", "instance") for h in home_dirs()]
    paths += config.setting("progress.instance_paths") or []
    return set(p.rstrip("/") for p in paths if p)


def in_instance(meta):
    """Was this session working in the instance (the owner's settings of this very system)?"""
    inst = config.instance_dir()
    remote = norm_remote((meta.get("git") or {}).get("remote"))
    if inst and remote and remote == norm_remote(origin_of(inst)):
        return True
    cwd = (meta.get("cwd") or "").rstrip("/")
    return any(cwd == p or cwd.startswith(p + "/") for p in instance_paths())


def engine_project(projects):
    """The project the engine itself is filed under: the instance's progress.instance_project; else the one
    named like the engine's repo or folder; else the only one that knows the engine's remote or folder.
    (Several can know the remote: sessions started in the engine's folder work on anything.)"""
    pid = config.setting("progress.instance_project")
    if pid in projects:
        return pid
    remote = norm_remote(origin_of(config.ENGINE))
    engine = os.path.realpath(config.ENGINE)
    for name in (remote.rsplit("/", 1)[-1], os.path.basename(engine)):
        if name in projects:
            return name
    known = [pid for pid, p in projects.items()
             if (remote and remote in [norm_remote(r) for r in p.get("remotes", [])])
             or engine in [os.path.realpath(x) for x in p.get("paths", []) if x]]
    return known[0] if len(known) == 1 else None


def load_projects():
    projects = {}
    for p in glob.glob(os.path.join(PROGRESS, "projects", "*", "project.json")):
        d = read_json(p, None)
        if d and d.get("id"):
            projects[d["id"]] = d
    return projects


def match_project(meta, projects):
    remote = norm_remote((meta.get("git") or {}).get("remote"))
    if remote:
        for pid, p in projects.items():
            if remote in [norm_remote(r) for r in p.get("remotes", [])]:
                return pid, "remote"
    cwd = (meta.get("cwd") or "").rstrip("/")
    best, best_len = None, 0
    for pid, p in projects.items():
        for path in p.get("paths", []):
            path = path.rstrip("/")
            if path and (cwd == path or cwd.startswith(path + "/")) and len(path) > best_len:
                best, best_len = pid, len(path)
    if best:
        return best, "path"
    if in_instance(meta):  # settings of this system are work on this system
        pid = engine_project(projects)
        if pid:
            return pid, "instance"
    return None, None


def learn_identity(project, meta):
    """Remember this session's remote/path so the next session matches by rule."""
    changed = False
    remote = (meta.get("git") or {}).get("remote")
    if remote and norm_remote(remote) not in [norm_remote(r) for r in project.setdefault("remotes", [])]:
        project["remotes"].append(remote)
        changed = True
    cwd = (meta.get("cwd") or "").rstrip("/")
    if not remote and cwd and not is_generic_dir(cwd) and cwd not in project.setdefault("paths", []):
        project["paths"].append(cwd)
        changed = True
    return changed


def project_brief(projects):
    out = []
    for pid, p in sorted(projects.items()):
        st = read_json(os.path.join(PROGRESS, "projects", pid, "status.json"), {})
        out.append({"id": pid, "name": p.get("name"), "description": p.get("description"),
                    "aliases": p.get("aliases", []), "headline": st.get("headline")})
    return out


# ---------------------------------------------------------------- sessions

def session_dirs():
    for mp in glob.glob(os.path.join(LOGS, "*", "*", "*", "meta.json")):
        yield os.path.dirname(mp)


def load_raw(d):
    """A session's records, its raw file in segments or not (chatlog.load_raw); a missing or wrong segment is said."""
    recs, problems = cl.load_raw(d)
    if problems:
        log("raw of %s not whole: %s" % (os.path.relpath(d, LOGS), "；".join(problems)))
    return recs


def assistant_texts(r):
    out = []
    for b in (r.get("message") or {}).get("content") or []:
        if isinstance(b, dict) and b.get("type") == "text" and b.get("text", "").strip():
            out.append(b["text"])
    return out


def record_lines(r):
    """Digest lines for one record, plus (user msgs, claude msgs, has report)."""
    lines, n_user, n_ai, report = [], 0, 0, False
    t = r.get("type")
    ts = cl.fmt_local(cl.parse_ts(r.get("timestamp")))[5:16]
    if t == "user":
        u = cl.user_text_of(r)
        if u:
            n_user += 1
            lines.append("[使用者 %s] %s" % (ts, clip(u, 2000)))
    elif t == "assistant":
        for text in assistant_texts(r):
            n_ai += 1
            report = report or MARKER in text
            lines.append("[Claude] %s" % (text if MARKER in text else clip(text, 1500)))
        for blk in (r.get("message") or {}).get("content") or []:
            if isinstance(blk, dict) and blk.get("type") == "tool_use":
                lines.append("[工具] %s" % cl.tool_hint(blk.get("name", "?"), blk.get("input") or {}))
    return lines, n_user, n_ai, report


def build_digest(recs, a, b):
    lines, n_user, n_ai, report = [], 0, 0, False
    for r in recs[a:b]:
        l, u, ai, rep = record_lines(r)
        lines += l
        n_user += u
        n_ai += ai
        report = report or rep
    text = "\n".join(lines)
    if len(text) > DIGEST_MAX:  # only when a single turn is enormous; segments are normally far smaller
        head, tail = text[:8000], text[-(DIGEST_MAX - 8000):]
        text = "%s\n…（中間省略 %d 字）…\n%s" % (head, len(text) - DIGEST_MAX, tail)
    return text, n_user, n_ai, report


def split_segments(recs, start):
    """Cut the new records into chunks at turn boundaries: a new chunk per local day, or when it grows past SEG_MAX.
    Long sessions spanning days then become one dated log entry per day instead of one blurred entry."""
    segs, seg_start, day, size = [], start, None, 0
    for i in range(start, len(recs)):
        r = recs[i]
        if r.get("type") == "user" and cl.user_text_of(r):
            ts = cl.parse_ts(r.get("timestamp"))
            d = ts.astimezone(TZ).date() if ts else day
            if i > seg_start and (d != day or size > SEG_MAX):
                segs.append((seg_start, i))
                seg_start, size = i, 0
            day = d
        size += sum(len(x) for x in record_lines(r)[0])
    if seg_start < len(recs):
        segs.append((seg_start, len(recs)))
    return segs


def seg_end_time(recs, a, b):
    for r in reversed(recs[a:b]):
        t = cl.parse_ts(r.get("timestamp"))
        if t:
            return t
    return None


def ask(system, prompt, schema, model):
    cmd = [CLAUDE, "-p", "--model", model, "--output-format", "json", "--no-session-persistence",
           "--tools", "", "--setting-sources", "", "--system-prompt", system, "--json-schema", json.dumps(schema)]
    env = background.env()  # a background Claude: the hooks every Claude runs leave it alone
    r = subprocess.run(cmd, input=prompt, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                       universal_newlines=True, timeout=900, env=env, cwd=WORK)
    try:
        d = json.loads(r.stdout)
    except ValueError:
        raise RuntimeError("claude failed (%d): %s" % (r.returncode, (r.stderr or r.stdout)[:300]))
    if d.get("is_error") or not isinstance(d.get("structured_output"), dict):
        raise RuntimeError("claude error: %s" % str(d.get("result"))[:300])
    return d["structured_output"], float(d.get("total_cost_usd") or 0)


LIST = {"type": "array", "items": {"type": "string"}}
SUMMARY_SCHEMA = {
    "type": "object", "required": ["entries"],
    "properties": {"entries": {"type": "array", "items": {
        "type": "object",
        "required": ["project", "summary", "done", "in_progress", "blockers", "decisions", "next", "why_project", "confidence"],
        "properties": {
            "project": {"type": "string"},
            "new_project": {"type": ["object", "null"], "properties": {
                "name": {"type": "string"}, "description": {"type": "string"}}},
            "summary": {"type": "string"}, "done": LIST, "in_progress": LIST, "blockers": LIST,
            "decisions": LIST, "next": LIST, "why_project": {"type": "string"},
            "confidence": {"type": "string", "enum": ["high", "medium", "low"]}}}}}}
STATUS_SCHEMA = {
    "type": "object", "required": ["state", "headline", "current", "recent_done", "blockers", "decisions", "next"],
    "properties": {"state": {"type": "string", "enum": ["active", "blocked", "paused", "done"]},
                   "headline": {"type": "string"}, "current": LIST, "recent_done": LIST, "blockers": LIST,
                   "decisions": LIST, "next": LIST}}


RECON_HINT = ("這段內容是從刪除前的殘留資料重建的：只有使用者打的字、貼上的內容、背景任務片段、同時段的 git commit，"
              "**沒有 AI 的回覆**。請從使用者的問題和 commit 推測做了什麼，不確定的寫成「可能」，不要編造結果。")


def summarize_session(meta, rel, digest, report, rule_pid, rule_how, projects, recon=False):
    lessons = read(os.path.join(PROGRESS, "prompts", "lessons.md"))
    info = {"host": meta.get("host"), "cwd": meta.get("cwd"), "git_remote": (meta.get("git") or {}).get("remote"),
            "branches": (meta.get("git") or {}).get("branches"), "title": meta.get("title"),
            "rule_project": rule_pid, "rule_method": rule_how, "contains_explicit_report": report}
    if recon:
        info["note"] = RECON_HINT
    prompt = "## 規則與教訓\n%s\n\n## 已知專案\n%s\n\n## Session 資料\n%s\n\n## 新內容\n%s\n" % (
        lessons, json.dumps(project_brief(projects), ensure_ascii=False, indent=1),
        json.dumps(info, ensure_ascii=False, indent=1), digest)
    return ask(read(os.path.join(PROGRESS, "prompts", "summarize.md")) or SUMMARIZE_PROMPT, prompt, SUMMARY_SCHEMA, MODEL)


def write_entry(pid, meta, rels, e, tag, when):
    loc = when.astimezone(TZ)
    web = logs_web()
    links = " ".join(("[%s](%s%s)" % (os.path.basename(r)[-8:], web, r)) if web else "`%s`" % r for r in rels)
    head = "### %s · %s · %s%s" % (loc.strftime("%Y-%m-%d %H:%M"), meta.get("host"), links, (" · " + tag) if tag else "")
    parts = [head, "", e.get("summary", "").strip(), ""]
    for label, key in (("完成", "done"), ("進行中", "in_progress"), ("卡住", "blockers"),
                       ("決定", "decisions"), ("下一步", "next")):
        if e.get(key):
            parts.append("**%s**\n%s\n" % (label, bullets(e[key])))
    append(os.path.join(PROGRESS, "projects", pid, "log", loc.strftime("%Y-%m") + ".md"), "\n".join(parts) + "\n")


# ---------------------------------------------------------------- status

STATE_LABEL = {"active": "進行中", "blocked": "卡住", "paused": "暫停", "done": "完成"}


def recent_log(pid, limit=15000):
    files = sorted(glob.glob(os.path.join(PROGRESS, "projects", pid, "log", "*.md")), reverse=True)
    out = ""
    for f in files:
        out = read(f) + "\n" + out
        if len(out) > limit:
            break
    return out[-limit:]


def update_status(pid, project):
    prev = read_json(os.path.join(PROGRESS, "projects", pid, "status.json"), {})
    prompt = "今天是 %s；這個專案最近一次活動是 %s（超過兩週沒動通常算 paused）。\n\n## 專案\n%s\n\n## 上一版狀態\n%s\n\n## 最近的進度日誌\n%s\n" % (
        datetime.now(TZ).strftime("%Y-%m-%d"), (prev.get("meta") or {}).get("last_activity_local", "?"),
        json.dumps({k: project.get(k) for k in ("id", "name", "description")}, ensure_ascii=False),
        json.dumps({k: v for k, v in prev.items() if k != "meta"}, ensure_ascii=False, indent=1) if prev else "（無）",
        recent_log(pid))
    st, cost = ask(read(os.path.join(PROGRESS, "prompts", "status.md")) or STATUS_PROMPT, prompt, STATUS_SCHEMA, STATUS_MODEL)
    st["meta"] = dict(prev.get("meta", {}), updated=now_utc().isoformat(timespec="seconds"))
    write_json(os.path.join(PROGRESS, "projects", pid, "status.json"), st)
    m = st["meta"]
    md = ["# %s — 最新進展" % project.get("name", pid), "",
          "**狀態**：%s · **最近活動**：%s · %s" % (STATE_LABEL.get(st["state"], st["state"]),
                                               m.get("last_activity_local", "?"), ", ".join(m.get("hosts", []))),
          "", "> %s" % st["headline"], ""]
    for label, key in (("目前在做", "current"), ("最近完成", "recent_done"), ("卡住／待決定", "blockers"),
                       ("已做的決定", "decisions"), ("下一步", "next")):
        if st.get(key):
            md += ["## %s" % label, bullets(st[key]), ""]
    md += ["---", "完整紀錄：[`log/`](log/) · 專案身分：[`project.json`](project.json) · 更新於 %s" %
           cl.fmt_local(cl.parse_ts(m["updated"]))]
    write(os.path.join(PROGRESS, "projects", pid, "STATUS.md"), "\n".join(md) + "\n")
    return cost


def project_activity(pid):
    """(entry count, last entry time text) from the append-only log, no LLM needed."""
    n, last = 0, ""
    for f in glob.glob(os.path.join(PROGRESS, "projects", pid, "log", "*.md")):
        for line in read(f).splitlines():
            if line.startswith("### "):
                n += 1
                last = max(last, line[4:20])
    return n, last


def write_root_status(projects, note=""):
    rows = []
    for pid, p in projects.items():
        st = read_json(os.path.join(PROGRESS, "projects", pid, "status.json"), {})
        n, last = project_activity(pid)
        if not n and not st:
            continue
        rows.append((st.get("meta", {}).get("last_activity_local") or last, pid, p, st, n))
    rows.sort(key=lambda r: (r[1] == MISC, "" if r[1] == MISC else r[0]), reverse=False)
    rows = sorted([r for r in rows if r[1] != MISC], key=lambda r: r[0], reverse=True) + [r for r in rows if r[1] == MISC]
    md = ["# 所有專案的最新狀態", "", "「進度」自動產生，更新於 %s。依最近活動排序。" % datetime.now(TZ).strftime("%Y-%m-%d %H:%M"), ""]
    if note:
        md += ["> " + note, ""]
    md += ["| 專案 | 狀態 | 現在 | 最近活動 | 紀錄數 | 機器 |", "|---|---|---|---|---|---|"]
    for last, pid, p, st, n in rows:
        m = st.get("meta", {})
        status_link = "projects/%s/STATUS.md" % pid if os.path.exists(os.path.join(PROGRESS, "projects", pid, "STATUS.md")) \
            else "projects/%s/log/" % pid
        md.append("| [%s](%s) | %s | %s | %s | %d | %s |" % (
            p.get("name", pid), status_link, STATE_LABEL.get(st.get("state"), "整理中") if st.get("state") else "整理中",
            (st.get("headline") or "").replace("|", "\\|"), (last or "")[:16], n, ", ".join(m.get("hosts", []))))
    if not rows:
        md.append("| （正在整理，稍後會出現） | | | | | |")
    write(os.path.join(PROGRESS, "STATUS.md"), "\n".join(md) + "\n")


# ---------------------------------------------------------------- run

def checkpoint(touched, projects, cursors, cursors_path, done_n, refresh=True, note=""):
    """Refresh STATUS for touched projects, save cursors, commit and push. Returns (cost, errors).
    refresh=False only records activity (used mid-backfill; STATUS is written once at the end)."""
    cost, errors = 0.0, 0
    for pid, t in touched.items():
        sp = os.path.join(PROGRESS, "projects", pid, "status.json")
        prev = read_json(sp, {})
        m = prev.setdefault("meta", {})
        hosts = set(m.get("hosts", [])) | t["hosts"]
        m.update(hosts=sorted(hosts), last_activity_utc=t["last"].isoformat(timespec="seconds"),
                 last_activity_local=cl.fmt_local(t["last"]))
        write_json(sp, prev)
        if not refresh:
            continue
        try:
            cost += update_status(pid, projects[pid])
        except Exception as e:
            errors += 1
            log("status failed %s: %s" % (pid, e))
    write_json(cursors_path, cursors)
    write_root_status(projects, note)

    git(PROGRESS, "add", "-A")
    if git(PROGRESS, "diff", "--cached", "--quiet").returncode != 0:
        msg = "progress: %d session(s) → %s" % (done_n, ", ".join(sorted(touched)) or "state")
        git(PROGRESS, "commit", "-q", "-m", msg)
        r = None
        if origin_of(PROGRESS):  # none: the record stays on this machine
            r = git(PROGRESS, "push", "-q", "-u", "origin", "HEAD", timeout=600)
            if r.returncode != 0:
                git(PROGRESS, "pull", "-q", "--rebase")
                r = git(PROGRESS, "push", "-q", "-u", "origin", "HEAD", timeout=600)
        if r is not None and r.returncode != 0:
            log("push failed: %s" % r.stderr.strip()[:300])
            errors += 1
    if refresh:
        touched.clear()
    return cost, errors


def recon_digest(d):
    """Body of a reconstructed session page, without the boilerplate note and metadata table."""
    lines = [l for l in read(os.path.join(d, "README.md")).splitlines()
             if not l.startswith(("> ", "| 欄位", "|---|---|\n")) and not re.match(r"^\| (主機|使用者|工作目錄|Git|時間|使用者訊息|同時段 commit|資料來源|Session) \|", l)]
    return "\n".join(lines)


def build_work(cursors, projects, now, backfill):
    """Everything to process, as (time, kind, payload), oldest first."""
    cut = None if backfill else now - timedelta(days=BOOTSTRAP_DAYS)
    work, groups, waiting = [], {}, 0
    for d in session_dirs():
        meta = read_json(os.path.join(d, "meta.json"), {})
        if not meta.get("session_id"):
            continue
        eps = set(meta.get("entrypoints") or [])
        if eps and eps <= SKIP_ENTRYPOINTS:
            continue
        key = "%s/%s" % (meta.get("host"), meta["session_id"])
        cur = cursors.get(key)
        end = cl.parse_ts(meta.get("end_utc"))
        rel = os.path.relpath(d, LOGS)
        if meta.get("reconstructed"):
            if cur is None and backfill:
                gk = (meta.get("host"), meta.get("cwd") or "", (meta.get("start_local") or "")[:10])
                groups.setdefault(gk, []).append((d, meta, key, rel, end))
            continue
        if cur and cur.get("end") == meta.get("end_utc") and not cur.get("skipped"):
            continue
        if cur and cur.get("skipped") and not backfill:
            continue
        if cur is None and cut and end and end < cut:
            cursors[key] = {"n": 0, "end": None, "skipped": "older than %d days" % BOOTSTRAP_DAYS}
            continue
        start = 0 if (cur or {}).get("skipped") else (cur or {}).get("n", 0)
        recs = load_raw(d)
        pending = []
        for sa, sb in split_segments(recs, start):
            digest, n_user, n_ai, rep = build_digest(recs, sa, sb)
            if n_user + n_ai:
                pending.append((sa, sb, digest, rep))
        if not pending:
            cursors[key] = {"n": len(recs), "end": meta.get("end_utc")}
            continue
        if not any(p[3] for p in pending) and end and now - end < timedelta(minutes=IDLE_MIN):
            waiting += 1
            continue  # still being worked on; wait until it settles
        for i, (sa, sb, digest, rep) in enumerate(pending):
            when = seg_end_time(recs, sa, sb) or end or now
            work.append((when, "chunk", {"meta": meta, "key": key, "rels": [rel], "sa": sa, "sb": sb,
                                         "digest": digest, "report": rep, "final": i == len(pending) - 1,
                                         "end_utc": meta.get("end_utc")}))
    for (host, cwd, day), items in groups.items():
        items.sort(key=lambda x: x[4] or now)
        batch, size = [], 0
        for it in items + [None]:
            text = None if it is None else "#### session %s：%s\n%s" % (
                it[1]["session_id"][:8], it[1].get("title", ""), recon_digest(it[0]))
            if batch and (it is None or size + len(text) > SEG_MAX):
                when = batch[-1][4] or now
                work.append((when, "recon", {"meta": batch[0][1], "keys": [b[2] for b in batch],
                                             "rels": [b[3] for b in batch], "ends": [b[1].get("end_utc") for b in batch],
                                             "digest": "\n\n".join(b[5] for b in batch)}))
                batch, size = [], 0
            if it is not None:
                batch.append(it + (clip(text, SEG_MAX),))
                size += len(text)
    work.sort(key=lambda w: w[0])
    return work, waiting


def run(dry=False, max_n=MAX_PER_RUN, backfill=False):
    try:
        ensure_repos()
    except NoLogsYet:
        if not dry:
            local = read_json(os.path.join(STATE_DIR, "state.json"), {})
            local.update(last_run=datetime.now(TZ).isoformat(timespec="seconds"), last_errors=0)
            write_json(os.path.join(STATE_DIR, "state.json"), local)
        return 0
    cursors_path = os.path.join(PROGRESS, "state", "cursors.json")
    cursors = read_json(cursors_path, {})
    projects = load_projects()
    now = now_utc()
    local = read_json(os.path.join(STATE_DIR, "state.json"), {})
    logs_head = git(LOGS, "rev-parse", "HEAD").stdout.strip()
    if not backfill and not dry and logs_head == local.get("logs_head") and not local.get("waiting"):
        # rule: no new commit in the logs repo (so no new work or /report) and nobody waiting to go idle -> do nothing
        local["last_run"] = datetime.now(TZ).isoformat(timespec="seconds")
        local["last_errors"] = 0
        write_json(os.path.join(STATE_DIR, "state.json"), local)
        return 0
    work, waiting = build_work(cursors, projects, now, backfill)
    total = len(work)
    if dry:
        kinds = {}
        for _, k, _w in work:
            kinds[k] = kinds.get(k, 0) + 1
        print("%d LLM call(s) would be made: %s" % (total, kinds))
        return 0

    calls, cost, touched, errors, blocked = 0, 0.0, {}, 0, set()
    all_touched = set()  # checkpoint() clears `touched`; keep a run-wide count for the log
    for when, kind, w in work:
        if calls >= max_n:
            break
        meta = w["meta"]
        key = w.get("key")
        if key in blocked:
            continue  # an earlier chunk of this session failed; keep order, retry next run
        rule_pid, rule_how = match_project(meta, projects)
        try:
            out, c = summarize_session(meta, w["rels"][0], w["digest"], w.get("report", False),
                                       rule_pid, rule_how, projects, recon=(kind == "recon"))
        except Exception as e:
            errors += 1
            log("summarize failed %s: %s" % (w["rels"][0], e))
            if key:
                blocked.add(key)
            continue
        calls += 1
        cost += c
        tag = "推測（只有使用者訊息）" if kind == "recon" else ("主動匯報" if w.get("report") else "")
        entries = out.get("entries", [])
        solo = len(set(e.get("project") for e in entries if e.get("project") != MISC)) == 1
        for e in entries:
            pid = e.get("project") or MISC
            if pid not in projects:
                pid = slug(pid)
            if pid not in projects:
                np = e.get("new_project") or {}
                projects[pid] = {"id": pid, "name": np.get("name") or pid, "description": np.get("description", ""),
                                 "remotes": [], "paths": [], "aliases": [], "created": now.isoformat(timespec="seconds")}
            if pid != MISC and solo:
                learn_identity(projects[pid], meta)
            write_json(os.path.join(PROGRESS, "projects", pid, "project.json"), projects[pid])
            write_entry(pid, meta, w["rels"], e, tag, when)
            append(os.path.join(PROGRESS, "sessions", when.astimezone(TZ).strftime("%Y-%m") + ".jsonl"),
                   json.dumps({"time": when.isoformat(timespec="seconds"), "host": meta.get("host"),
                               "sessions": [os.path.basename(r)[-8:] for r in w["rels"]], "project": pid,
                               "method": rule_how if rule_pid == pid else "llm", "confidence": e.get("confidence"),
                               "why": e.get("why_project"), "kind": kind,
                               "records": [w.get("sa"), w.get("sb")] if kind == "chunk" else None,
                               "logs": w["rels"]}, ensure_ascii=False) + "\n")
            all_touched.add(pid)
            t = touched.setdefault(pid, {"hosts": set(), "last": when})
            t["hosts"].add(meta.get("host"))
            t["last"] = max(t["last"], when)
        if kind == "chunk":
            cursors[key] = {"n": w["sb"], "end": w["end_utc"] if w["final"] else None}
        else:
            for k2, end2 in zip(w["keys"], w["ends"]):
                cursors[k2] = {"n": 0, "end": end2, "recon": True}
        if calls % CHECKPOINT == 0:  # publish progress as we go
            note = "整理過去的對話中：%d / %d" % (calls, total) if backfill else ""
            c2, e2 = checkpoint(touched, projects, cursors, cursors_path, calls, refresh=not backfill, note=note)
            cost += c2
            errors += e2

    if calls:
        c2, e2 = checkpoint(touched, projects, cursors, cursors_path, calls)
        cost += c2
        errors += e2
    else:
        # nothing filed: keep cursor bookkeeping locally, it rides along with the next real commit
        write_json(cursors_path, cursors)
    st = read_json(os.path.join(STATE_DIR, "state.json"), {})
    st.update(logs_head=logs_head, waiting=waiting + max(0, total - calls),
              last_run=datetime.now(TZ).isoformat(timespec="seconds"), last_processed=calls,
              total_cost_usd=round(st.get("total_cost_usd", 0) + cost, 4), last_errors=errors)
    write_json(os.path.join(STATE_DIR, "state.json"), st)
    if calls or errors:
        log("run: %d call(s), %d project(s), $%.3f, %d error(s)%s" % (calls, len(all_touched), cost, errors,
                                                                    " [backfill]" if backfill else ""))
    return 1 if errors else 0


def run_noted(dry=False, max_n=MAX_PER_RUN, backfill=False):
    """run(), with a round that stops on an error noted in state.json ("last_failed": when, and its last line) for
    doctor, which otherwise saw only "not run yet" while every round failed (2026-10-15). A round that ends clears it."""
    path = os.path.join(STATE_DIR, "state.json")
    try:
        rc = run(dry, max_n, backfill)
    except BaseException as e:
        if isinstance(e, KeyboardInterrupt) or (isinstance(e, SystemExit) and not isinstance(e.code, str) and not e.code):
            raise
        why = str(e.code if isinstance(e, SystemExit) else e) or type(e).__name__
        st = read_json(path, {})
        st["last_failed"] = {"at": datetime.now(TZ).isoformat(timespec="seconds"),
                             "why": clip(why.strip().splitlines()[-1] if why.strip() else why, 300)}
        write_json(path, st)
        raise
    st = read_json(path, {})
    if not dry and "last_failed" in st:
        del st["last_failed"]
        write_json(path, st)
    return rc


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd")
    r = sub.add_parser("run")
    r.add_argument("--dry-run", action="store_true")
    r.add_argument("--max", type=int, default=MAX_PER_RUN)
    r.add_argument("--backfill", action="store_true", help="also process everything older than %d days and reconstructed history" % BOOTSTRAP_DAYS)
    sub.add_parser("status")
    a = ap.parse_args()
    if a.cmd == "run":
        os.makedirs(STATE_DIR, exist_ok=True)
        with open(os.path.join(STATE_DIR, "lock"), "w") as lf:
            try:
                fcntl.flock(lf, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                return 0  # previous run still going
            return run_noted(a.dry_run, a.max, a.backfill)
    if a.cmd == "status":
        print(json.dumps(read_json(os.path.join(STATE_DIR, "state.json"), {}), ensure_ascii=False, indent=1))
        print("".join(read(os.path.join(STATE_DIR, "progress.log")).splitlines(True)[-8:]))
        return 0
    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
