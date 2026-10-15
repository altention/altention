#!/usr/bin/env python3
"""chatlog: mirror Claude Code transcripts into a git repo as browsable Markdown + raw JSONL.

Usage:
  chatlog.py hook            # called by Claude Code hooks (Stop / SessionEnd); reads hook JSON on stdin
  chatlog.py export PATH [--sync]
  chatlog.py sync [--all]    # export changed transcripts, commit, push to the logs repo (its cron line); the engine
                             # and the instance are kept in step by altention/cron_sync.py, not here (3a.10)
  chatlog.py status
  chatlog.py raw FOLDER      # a session's raw file, its segments joined (exit 1, and why, if one is missing or wrong)
  chatlog.py split [FOLDER...] [--over MB]   # this machine's big raw.jsonl files put in segments from themselves

Compatible with Python 3.8+.
"""
import argparse
import fcntl
import getpass
import glob
import html
import json
import os
import platform
import re
import socket
import subprocess
import sys
import time
import zlib
from collections import Counter
from datetime import datetime, timedelta, timezone

CLAUDE_DIR = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude")
DATA = os.environ.get("ALT_DATA") or os.path.join(os.environ.get("ALT_ROOT") or "~/altention", "data")  # as {data} in plugin.json
REPO = os.path.expanduser(os.environ.get("CHATLOG_REPO") or os.path.join(DATA, "logs"))
STATE_DIR = os.path.expanduser(os.environ.get("CHATLOG_STATE", "~/.cache/chatlog"))
def _host():
    """Stable machine name: env, then the engine's own (altention.config.this_machine: ~/.config/altention/host, the
    machine list's name for it), so that the logs repo has one folder per machine whatever names it; when that cannot
    be read, the host file or the hostname (macOS hostnames change with the network)."""
    if os.environ.get("CHATLOG_HOST"):
        return os.environ["CHATLOG_HOST"]
    try:
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        from altention import config
        name = config.this_machine()
        if name:
            return name
    except Exception:  # a broken list or a copy without the engine beside it: as before
        pass
    try:
        with open(os.path.expanduser("~/.config/altention/host")) as f:
            name = f.read().strip()
            if name:
                return name
    except OSError:
        pass
    return socket.gethostname().split(".")[0]


HOST = _host()
TZ_HOURS = float(os.environ.get("CHATLOG_TZ_OFFSET") or time.localtime().tm_gmtoff / 3600)  # else this machine's
TZ = timezone(timedelta(hours=TZ_HOURS))
TZ_LABEL = "UTC%+g" % TZ_HOURS

RAW_FIELD_MAX = 20000      # longer strings in raw.jsonl are truncated
MD_BLOCK_MAX = 3000        # tool input/output shown in Markdown
MD_PART_MAX = 400000       # split Markdown into parts above this size (GitHub render limit)
GITHUB_FILE_MAX = 90 * 1024 * 1024


# ---------------------------------------------------------------- utilities

def log(msg):
    os.makedirs(STATE_DIR, exist_ok=True)
    path = os.path.join(STATE_DIR, "chatlog.log")
    try:
        if os.path.getsize(path) > 2 * 1024 * 1024:
            os.replace(path, path + ".1")
    except OSError:
        pass
    with open(path, "a") as f:
        f.write("%s [%s] %s\n" % (datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S"), HOST, msg))


class Lock(object):
    def __enter__(self):
        os.makedirs(STATE_DIR, exist_ok=True)
        self.f = open(os.path.join(STATE_DIR, "lock"), "w")
        fcntl.flock(self.f, fcntl.LOCK_EX)
        return self

    def __exit__(self, *a):
        fcntl.flock(self.f, fcntl.LOCK_UN)
        self.f.close()


def load_state():
    try:
        with open(os.path.join(STATE_DIR, "state.json")) as f:
            st = json.load(f)
        if isinstance(st, dict) and isinstance(st.get("sigs"), dict):
            return st
    except (OSError, ValueError):
        pass
    return {"sigs": {}, "last_sync": None, "last_error": None}


def save_state(st):
    os.makedirs(STATE_DIR, exist_ok=True)
    tmp = os.path.join(STATE_DIR, "state.json.tmp")
    with open(tmp, "w") as f:
        json.dump(st, f, indent=1)
    os.replace(tmp, os.path.join(STATE_DIR, "state.json"))


def git(*args, **kw):
    return subprocess.run(["git", "-C", REPO] + list(args), stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, universal_newlines=True, timeout=kw.get("timeout", 120))


def parse_ts(s):
    if not s or not isinstance(s, str):
        return None
    try:
        s = s.replace("Z", "+00:00")
        m = re.match(r"(.*\.\d{1,6})\d*(\+.*)$", s)  # trim >6 fractional digits
        if m:
            s = m.group(1) + m.group(2)
        return datetime.fromisoformat(s)
    except ValueError:
        return None


def fmt_local(dt):
    return dt.astimezone(TZ).strftime("%Y-%m-%d %H:%M:%S") if dt else "?"


def redact(text):
    """altention/redact.py, shared with the bot (what is hidden from the logs is hidden from Discord). Loaded here, not
    at the top: the hook of every Claude on the machine loads this file and must not fail for the engine's shared
    code. Where it does not load, the export fails (logged) rather than push anything unredacted."""
    global _redact
    if _redact is None:
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        from altention.redact import redact as shared
        _redact = shared
    return _redact(text)


_redact = None


def clip(text, n):
    if len(text) <= n:
        return text
    return text[:n] + "\n…（已截斷，原長 %d 字元）" % len(text)


def fence(text, lang=""):
    n = max([3] + [len(m) + 1 for m in re.findall(r"`{3,}", text)])
    f = "`" * n
    return "%s%s\n%s\n%s" % (f, lang, text, f)


def details(summary, body):
    return "<details><summary>%s</summary>\n\n%s\n\n</details>\n" % (html.escape(summary), body)


_CODE = re.compile(r"(```[\s\S]*?```|`[^`\n]+`)")


def escape_tags(text):
    """Escape <tag>-like text outside code so GitHub doesn't swallow it."""
    parts = _CODE.split(text)
    for i in range(0, len(parts), 2):
        parts[i] = re.sub(r"<(?=[A-Za-z/!])", "&lt;", parts[i])
    return "".join(parts)


def slugify(s):
    s = re.sub(r"[^\w.-]+", "-", s or "session").strip("-")
    return (s or "session")[:40]


def load_jsonl(path):
    recs = []
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    recs.append(json.loads(line))
                except ValueError:
                    pass
    return recs


def shrink(obj):
    """Truncate huge strings (base64 images, giant tool outputs) for raw.jsonl."""
    if isinstance(obj, str):
        if len(obj) > RAW_FIELD_MAX:
            return obj[:RAW_FIELD_MAX] + "…[truncated %d chars]" % len(obj)
        return obj
    if isinstance(obj, list):
        return [shrink(x) for x in obj]
    if isinstance(obj, dict):
        if obj.get("type") == "base64" and isinstance(obj.get("data"), str):
            return dict(obj, data="[base64 omitted, %d bytes]" % len(obj["data"]))
        return dict((k, shrink(v)) for k, v in obj.items())
    return obj


# ---------------------------------------------------------------- metadata

def git_info(cwd):
    if not cwd or not os.path.isdir(cwd):
        return {}
    info = {}
    for key, args in (("remote", ["remote", "get-url", "origin"]),
                      ("commit", ["rev-parse", "--short", "HEAD"]),
                      ("branch", ["rev-parse", "--abbrev-ref", "HEAD"])):
        try:
            r = subprocess.run(["git", "-C", cwd] + args, stdout=subprocess.PIPE,
                               stderr=subprocess.DEVNULL, universal_newlines=True, timeout=5)
            if r.returncode == 0 and r.stdout.strip():
                info[key] = r.stdout.strip()
        except Exception:
            pass
    return info


def user_text_of(rec):
    """Return the user's own typed text in a record, or None."""
    if rec.get("type") != "user" or rec.get("isMeta") or rec.get("isCompactSummary"):
        return None
    c = (rec.get("message") or {}).get("content")
    texts = []
    if isinstance(c, str):
        texts = [c]
    elif isinstance(c, list):
        texts = [b.get("text", "") for b in c if isinstance(b, dict) and b.get("type") == "text"]
    out = []
    for t in texts:
        t = t.strip()
        if not t or t.startswith(("<local-command-caveat>", "<local-command-stdout>", "<task-notification>",
                                  "<system-reminder>", "[Request interrupted")):
            continue
        m = re.search(r"<command-name>(.*?)</command-name>", t, re.S)
        if m:
            a = re.search(r"<command-args>(.*?)</command-args>", t, re.S)
            t = (m.group(1).strip() + " " + (a.group(1).strip() if a else "")).strip()
        out.append(t)
    return "\n\n".join(out) if out else None


def collect_meta(recs, path, sid):
    times = [t for t in (parse_ts(r.get("timestamp")) for r in recs) if t]
    models, tools = Counter(), Counter()
    versions, branches, entrypoints, cwds, perms = [], [], [], [], []
    usage = Counter()
    title, env, bridge, prompts = None, {}, None, 0

    def add(lst, v):
        if v and v not in lst:
            lst.append(v)

    for r in recs:
        t = r.get("type")
        add(versions, r.get("version"))
        if r.get("gitBranch") != "HEAD":
            add(branches, r.get("gitBranch"))
        add(entrypoints, r.get("entrypoint"))
        add(cwds, r.get("cwd"))
        add(perms, r.get("permissionMode"))
        if t == "ai-title":
            title = r.get("aiTitle") or title
        elif t == "attachment" and isinstance(r.get("attachment"), dict):
            a = r["attachment"]
            if a.get("type") == "environment" and not env:
                env = a.get("snapshot") or {}
        elif t == "system" and r.get("subtype") == "bridge_status" and r.get("url"):
            bridge = r["url"]
        elif t == "assistant":
            m = r.get("message") or {}
            if m.get("model") and m["model"] != "<synthetic>":
                models[m["model"]] += 1
            for k, v in (m.get("usage") or {}).items():
                if isinstance(v, int):
                    usage[k] += v
            for b in m.get("content") or []:
                if isinstance(b, dict) and b.get("type") == "tool_use":
                    tools[b.get("name", "?")] += 1
        elif t == "user" and user_text_of(r):
            prompts += 1

    if not title:
        for r in recs:
            u = user_text_of(r)
            if u and not u.startswith("/"):
                title = re.sub(r"\s+", " ", u)[:60]
                break
    cwd = cwds[0] if cwds else ""
    start, end = (min(times), max(times)) if times else (None, None)
    return {
        "session_id": sid,
        "title": redact(title or "(untitled)"),
        "host": HOST,
        "user": getpass.getuser(),
        "os": platform.platform(),
        "env": {k: env.get(k) for k in ("platform", "osVersion", "shell", "isGitRepo", "isWorktree") if k in env},
        "cwd": cwd,
        "cwds": cwds,
        "project": os.path.basename(cwd.rstrip("/")) if cwd else os.path.basename(os.path.dirname(path)),
        "git": dict(git_info(cwd), branches=branches),
        "start_utc": start.astimezone(timezone.utc).isoformat() if start else None,
        "end_utc": end.astimezone(timezone.utc).isoformat() if end else None,
        "start_local": fmt_local(start) if start else None,
        "end_local": fmt_local(end) if end else None,
        "tz": TZ_LABEL,
        "duration_min": round((end - start).total_seconds() / 60, 1) if start else None,
        "claude_code_versions": versions,
        "entrypoints": entrypoints,
        "permission_modes": perms,
        "models": dict(models),
        "user_prompts": prompts,
        "assistant_messages": sum(models.values()),
        "tools": dict(tools.most_common()),
        "tokens": {k: usage[k] for k in ("input_tokens", "output_tokens", "cache_read_input_tokens",
                                         "cache_creation_input_tokens") if usage[k]},
        "remote_control_url": bridge,
        "transcript_path": path,
    }


# ---------------------------------------------------------------- markdown

def tool_hint(name, inp):
    for k in ("description", "command", "file_path", "pattern", "url", "query", "prompt", "skill"):
        v = inp.get(k) if isinstance(inp, dict) else None
        if isinstance(v, str) and v.strip():
            return "%s — %s" % (name, re.sub(r"\s+", " ", v)[:90])
    return name


def result_text(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        out = []
        for b in content:
            if isinstance(b, dict):
                if b.get("type") == "text":
                    out.append(b.get("text", ""))
                elif b.get("type") == "image":
                    out.append("[圖片]")
        return "\n".join(out)
    return json.dumps(content, ensure_ascii=False)


def render_turns(recs):
    """Return list of Markdown chunks, one per turn, for splitting into parts."""
    results = {}
    for r in recs:
        if r.get("type") == "user":
            c = (r.get("message") or {}).get("content")
            if isinstance(c, list):
                for b in c:
                    if isinstance(b, dict) and b.get("type") == "tool_result":
                        results[b.get("tool_use_id")] = (result_text(b.get("content")), b.get("is_error"))

    turns, cur, last_role = [], [], None

    def flush():
        if cur:
            turns.append("\n".join(cur))
            del cur[:]

    for r in recs:
        t = r.get("type")
        ts = fmt_local(parse_ts(r.get("timestamp")))
        if t == "user":
            if r.get("isCompactSummary"):
                flush()
                c = result_text((r.get("message") or {}).get("content"))
                cur.append("---\n\n" + details("對話壓縮摘要 · " + ts, fence(clip(c, 20000))))
                last_role = None
                continue
            u = user_text_of(r)
            c = (r.get("message") or {}).get("content")
            if u:
                flush()
                cur.append("### 使用者 · %s\n\n%s\n" % (ts, escape_tags(u)))
                last_role = "user"
            elif isinstance(c, str) and c.lstrip().startswith("<task-notification>"):
                cur.append(details("背景任務通知 · " + ts, fence(clip(c, MD_BLOCK_MAX))))
            elif isinstance(c, str) and c.lstrip().startswith("<local-command-stdout>"):
                out = re.sub(r"</?local-command-stdout>", "", c).strip()
                if out:
                    cur.append(fence(clip(out, MD_BLOCK_MAX)) + "\n")
            elif r.get("isMeta") and isinstance(c, (str, list)):
                txt = result_text(c)
                if txt.strip() and not txt.lstrip().startswith("<local-command-caveat>"):
                    cur.append(details("系統注入內容 · " + ts, fence(clip(txt, MD_BLOCK_MAX))))
        elif t == "assistant":
            m = r.get("message") or {}
            if last_role != "assistant":
                cur.append("### Claude · %s · %s\n" % (ts, m.get("model", "")))
                last_role = "assistant"
            for b in m.get("content") or []:
                if not isinstance(b, dict):
                    continue
                bt = b.get("type")
                if bt == "text" and b.get("text", "").strip():
                    cur.append(escape_tags(b["text"]) + "\n")
                elif bt == "thinking" and b.get("thinking", "").strip():
                    cur.append(details("思考過程", clip(b["thinking"], 20000)))
                elif bt == "tool_use":
                    inp = b.get("input") or {}
                    body = fence(clip(json.dumps(inp, ensure_ascii=False, indent=2), MD_BLOCK_MAX), "json")
                    res = results.get(b.get("id"))
                    if res is not None:
                        label = "錯誤" if res[1] else "結果"
                        body += "\n\n**%s**\n\n%s" % (label, fence(clip(res[0], MD_BLOCK_MAX)))
                    cur.append(details("工具：" + tool_hint(b.get("name", "?"), inp), body))
        elif t == "system":
            st = r.get("subtype")
            if st == "compact_boundary":
                flush()
                cur.append("---\n\n*（%s 對話在此壓縮）*\n" % ts)
            elif st not in ("turn_duration", "bridge_status", "away_summary") and r.get("content"):
                cur.append(details("系統訊息（%s）· %s" % (st, ts), fence(clip(str(r["content"]), MD_BLOCK_MAX))))
    flush()
    return turns


def meta_table(meta, extra_rows=()):
    g = meta.get("git") or {}
    tok = meta.get("tokens") or {}
    rows = [
        ("主機", "%s（%s）" % (meta["host"], meta["os"])),
        ("使用者", meta["user"]),
        ("工作目錄", "`%s`" % meta["cwd"]),
        ("Git", " · ".join(x for x in [g.get("remote"), ", ".join(g.get("branches") or []) or g.get("branch"),
                                        g.get("commit") and "export 時 HEAD " + g["commit"]] if x) or "—"),
        ("時間", "%s → %s（%s，約 %s 分鐘）" % (meta["start_local"], meta["end_local"], meta["tz"],
                                             meta["duration_min"])),
        ("模型", ", ".join("%s ×%d" % kv for kv in meta["models"].items()) or "—"),
        ("Claude Code", ", ".join(meta["claude_code_versions"]) + "（" + ", ".join(meta["entrypoints"]) + "）"),
        ("權限模式", ", ".join(meta["permission_modes"]) or "—"),
        ("訊息數", "使用者 %d · Claude %d" % (meta["user_prompts"], meta["assistant_messages"])),
        ("工具", ", ".join("%s ×%d" % kv for kv in list(meta["tools"].items())[:12]) or "—"),
        ("Token", " · ".join("%s %s" % (k.replace("_input_tokens", "").replace("_tokens", ""), format(v, ","))
                             for k, v in tok.items()) or "—"),
        ("Session", "`%s`" % meta["session_id"]),
    ]
    if meta.get("remote_control_url"):
        rows.append(("Remote Control", meta["remote_control_url"]))
    rows.extend(extra_rows)
    return "| 欄位 | 值 |\n|---|---|\n" + "\n".join(
        "| %s | %s |" % (k, str(v).replace("|", "\\|").replace("\n", " ")) for k, v in rows)


# ---------------------------------------------------------------- export

def find_existing_dir(sid):
    for mp in glob.glob(os.path.join(REPO, "*", "*", "*_%s" % sid[:8], "meta.json")):
        try:
            with open(mp) as f:
                m = json.load(f)
            if isinstance(m, dict) and m.get("session_id") == sid:
                return os.path.dirname(mp)
        except (OSError, ValueError):
            pass
    return None


def write(path, text):
    """Write only when content differs, so unchanged sessions leave no diff. Through a temporary file, so a write
    cut short leaves the old file whole (meta.json is what says which raw segments are the record)."""
    tmp = path + TMP
    if os.path.exists(tmp):  # left by a run that was killed: never committed into the logs repo
        os.remove(tmp)
    try:
        with open(path, encoding="utf-8") as f:
            if f.read() == text:
                return False
    except (OSError, UnicodeDecodeError):
        pass
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
    return True


def write_raw(path, recs):
    lines = [redact(json.dumps(shrink(r), ensure_ascii=False)) for r in recs]
    data = "\n".join(lines) + "\n"
    if len(data.encode("utf-8")) > GITHUB_FILE_MAX:
        import gzip
        with gzip.open(path + ".gz", "wt", encoding="utf-8") as f:
            f.write(data)
        if os.path.exists(path):
            os.remove(path)
        log("raw too large, gzipped: " + path)
    else:
        write(path, data)


# ---------------------------------------------------------------- raw in segments (manager 2026-10-07)
# A raw file past SEG_MAX is kept as <base>-0001.jsonl.gz, <base>-0002.jsonl.gz, ... (full segments of about
# SEG_MAX bytes, cut at line ends, never changed again: git stores each once) and an open tail <base>-NNNN.jsonl
# that goes on growing; meta.json lists them with their lines and sha256 (of the uncompressed bytes) and is the one
# thing that says which files are the record: new segments are written and checked first, meta.json is replaced,
# only then is what they replace removed. A run killed anywhere leaves the record meta.json names whole.

SEG_MAX = 8 * 1024 * 1024  # bytes of a full segment, uncompressed (manager 2026-10-07)
TMP = ".chatlog-tmp"       # what chatlog writes before renaming into place


def raw_lines(recs):
    """The lines of a raw file, as bytes each ending in a newline (what write_raw writes, line by line)."""
    return [(redact(json.dumps(shrink(r), ensure_ascii=False)) + "\n").encode("utf-8") for r in recs]


def _sha(data):
    import hashlib
    return hashlib.sha256(data).hexdigest()


def _gzip(data):
    """Always the same bytes for the same data on one machine: no time or name in the header."""
    import gzip
    import io
    buf = io.BytesIO()
    with gzip.GzipFile(filename="", mode="wb", fileobj=buf, mtime=0, compresslevel=9) as g:
        g.write(data)
    return buf.getvalue()


def _gunzip(data):
    import gzip
    return gzip.decompress(data)


def seg_name(base, i, sealed):
    return "%s-%04d.jsonl%s" % (base, i, ".gz" if sealed else "")


def _own(base):
    """The file names segments of `base` and their temporary files have (the only ones cleanup ever touches)."""
    return re.compile(r"^%s-\d{4}\.jsonl(\.gz)?(%s)?$" % (re.escape(os.path.basename(base)), re.escape(TMP)))


def plan_segments(lines, keep):
    """Line counts of the full segments: those already sealed (`keep`, from meta.json) as long as the lines cover
    them, then new ones of up to SEG_MAX bytes while what is left is more than SEG_MAX. The rest is the tail."""
    counts, pos = [], 0
    for n in keep:
        if pos + n > len(lines):
            break
        counts.append(n)
        pos += n
    rest = sum(len(l) for l in lines[pos:])
    while rest > SEG_MAX:
        n = size = 0
        while pos + n < len(lines) and (n == 0 or size + len(lines[pos + n]) <= SEG_MAX):
            size += len(lines[pos + n])
            n += 1
        counts.append(n)
        pos += n
        rest -= size
    return counts


def _put(path, blob, sha, sealed):
    """Write `blob` into place through a temporary file, read back and checked against `sha` first."""
    tmp = path + TMP
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(tmp, "wb") as f:
        f.write(blob)
        f.flush()
        os.fsync(f.fileno())
    with open(tmp, "rb") as f:
        back = f.read()
    if _sha(_gunzip(back) if sealed else back) != sha:
        os.remove(tmp)
        raise IOError("寫進去讀回來不一樣：%s" % path)
    os.replace(tmp, path)


def _holds(path, chunk, sealed):
    """Is `chunk` what the file at `path` has (read, unzipped)? Checked every time, not by its size: a file damaged
    in place (#code-review-2) is written again by the next export instead of being pushed as it is."""
    try:
        with open(path, "rb") as f:
            data = f.read()
        return (_gunzip(data) if sealed else data) == chunk
    except (OSError, EOFError, ValueError, zlib.error):
        return False


def write_segments(folder, base, lines, old=None):
    """Write `lines` as segments of `base` in `folder` and return what meta.json keeps of them. A segment meta.json
    already lists with the same content is not written again. Nothing is removed here (cleanup_segments, after
    meta.json)."""
    olds = {x["file"]: x for x in (old or {}).get("segments", [])}
    counts = plan_segments(lines, [x["lines"] for x in (old or {}).get("segments", [])[:-1]])
    segs, pos = [], 0
    for i, n in enumerate(counts + [len(lines) - sum(counts)], 1):
        chunk = b"".join(lines[pos:pos + n])
        pos += n
        sealed = i <= len(counts)
        name = seg_name(base, i, sealed)
        path = os.path.join(folder, name)
        info = {"file": name, "lines": n, "bytes": len(chunk), "sha256": _sha(chunk)}
        was = olds.get(name)
        if was and was.get("sha256") == info["sha256"] and _holds(path, chunk, sealed):
            segs.append(dict(info, **({"gz_bytes": os.path.getsize(path)} if sealed else {})))
            continue
        if was and was.get("sha256") == info["sha256"]:
            log("segment on disk was not what meta.json lists, written again: %s" % path)
        elif was and sealed:
            log("sealed segment rewritten (its content changed, e.g. redaction): %s" % path)
        blob = _gzip(chunk) if sealed else chunk
        _put(path, blob, info["sha256"], sealed)
        segs.append(dict(info, **({"gz_bytes": len(blob)} if sealed else {})))
    whole = b"".join(lines)
    return {"format": 1, "lines": len(lines), "bytes": len(whole), "sha256": _sha(whole), "segments": segs}


def cleanup_segments(folder, base, info):
    """After meta.json lists `info`: remove the whole <base>.jsonl it replaces and any segment or temporary file of
    `base` it does not list. No other file is touched."""
    keep = {os.path.basename(x["file"]) for x in info["segments"]}
    where = os.path.join(folder, os.path.dirname(base))
    own = _own(base)
    for name in sorted(os.listdir(where)) if os.path.isdir(where) else []:
        if (own.match(name) and name not in keep) or name == os.path.basename(base) + ".jsonl":
            os.remove(os.path.join(where, name))
            log("removed %s (replaced by the segments meta.json lists)" % os.path.join(where, name))


def raw_info(folder, base="raw"):
    """What meta.json says of the segments of `base` (None: not in segments)."""
    try:
        with open(os.path.join(folder, "meta.json"), encoding="utf-8") as f:
            meta = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(meta, dict):
        return None
    subs = meta.get("subagents_raw")
    info = meta.get("raw") if base == "raw" else (subs.get(os.path.basename(base)) if isinstance(subs, dict) else None)
    ok = isinstance(info, dict) and isinstance(info.get("lines"), int) and not isinstance(info["lines"], bool) and \
        isinstance(info.get("sha256"), str) and isinstance(info.get("segments"), list) and info["segments"] and all(
        isinstance(x, dict) and isinstance(x.get("file"), str) and isinstance(x.get("lines"), int)
        and not isinstance(x["lines"], bool) and x["lines"] >= 0 and isinstance(x.get("sha256"), str)
        for x in info["segments"])
    # the numbers must also add up: the whole's lines is the protection against a shorter source (manager 2026-10-08)
    ok = ok and info["lines"] == sum(x["lines"] for x in info["segments"])
    return info if ok else None  # anything else is read as "not listed": said when its segments are there


def _meta_problem(folder):
    """Why the folder's meta.json cannot be read, or "" when it can."""
    try:
        with open(os.path.join(folder, "meta.json"), encoding="utf-8") as f:
            meta = json.load(f)
        return "" if isinstance(meta, dict) else "meta.json 不是物件"
    except FileNotFoundError:
        return "沒有 meta.json（%s）" % folder
    except (OSError, ValueError) as e:
        return "meta.json 讀不出來（%s）" % e


def _unlisted(folder, base, listed):
    """Files of `base`'s own names in `folder` that `listed` does not name (the whole <base>.jsonl too, when listed)."""
    where = os.path.join(folder, os.path.dirname(base))
    own = _own(base)
    names = sorted(os.listdir(where)) if os.path.isdir(where) else []
    return [n for n in names if (own.match(n) and n not in listed)
            or (listed and n == os.path.basename(base) + ".jsonl")]


def unsure(folder):
    """The raw files of `folder` ("raw", "subagents/<id>") that have segments of their own names there while
    meta.json gives no complete list of them (not there, cut short, not an object, a segment without file, lines or
    sha256, or the whole's lines or sha256). What is kept is then not known, so export leaves the whole conversation
    alone (manager 2026-10-08). Not counted: a meta.json that can be read and is whole and has no list for that file
    at all (the files are left by a run that did not finish: never the record), and a folder whose meta.json is not
    there and was never committed (its first export stopped before meta.json)."""
    try:
        with open(os.path.join(folder, "meta.json"), encoding="utf-8") as f:
            meta = json.load(f)
    except FileNotFoundError:
        if not _ever_committed(os.path.join(folder, "meta.json")):
            return []  # a first export stopped before its meta.json: nothing here was ever the record
        meta = None
    except (OSError, ValueError):
        meta = None
    bases = ["raw"]
    sub = os.path.join(folder, "subagents")
    if os.path.isdir(sub):
        bases += sorted({"subagents/" + m.group(1) for m in
                         (re.match(r"^(.+)-\d{4}\.jsonl(\.gz)?(%s)?$" % re.escape(TMP), n) for n in os.listdir(sub)) if m})
    out = []
    for base in bases:
        if raw_info(folder, base) or not _unlisted(folder, base, set()):
            continue
        if base == "raw":
            entry_absent = isinstance(meta, dict) and "raw" not in meta
        else:
            subs = meta.get("subagents_raw") if isinstance(meta, dict) else None
            entry_absent = isinstance(meta, dict) and (subs is None or (isinstance(subs, dict) and os.path.basename(base) not in subs))
        if entry_absent:
            continue  # a whole meta.json that lists none of it: those files never were the record (manager 2026-10-08)
        out.append(base)
    return out


def _ever_committed(path):
    """Was `path` (in the logs repo) ever committed? Not known (git fails): taken as yes, so nothing is touched."""
    try:
        any_commit = git("rev-list", "-n", "1", "--all", timeout=30)
        if any_commit.returncode == 0 and not any_commit.stdout.strip():
            return False  # a repo with no commits yet
        r = git("log", "-1", "--format=%H", "--all", "--", os.path.relpath(path, REPO), timeout=30)
    except Exception:
        return True
    return r.returncode != 0 or bool(r.stdout.strip())


SAY_AGAIN = 3600  # seconds: the same line about one folder is written into chatlog.log at most this often


def say_now(key, now=None):
    """Should the line `key` be written now? Once per SAY_AGAIN (kept in STATE_DIR/said.json); never raises."""
    now = time.time() if now is None else now
    path = os.path.join(STATE_DIR, "said.json")
    try:
        with open(path) as f:
            said = json.load(f)
        said = said if isinstance(said, dict) else {}
    except (OSError, ValueError):
        said = {}
    at = said.get(key)
    if isinstance(at, (int, float)) and now - at < SAY_AGAIN:
        return False
    said = {k: v for k, v in said.items() if isinstance(v, (int, float)) and now - v < 7 * 86400}
    said[key] = now
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
        with open(path + TMP, "w") as f:
            json.dump(said, f)
        os.replace(path + TMP, path)
    except OSError:
        pass
    return True


def read_raw(folder, base="raw", info=None, notes=None):
    """(the raw file's bytes, [problem]): its segments joined as meta.json lists them, each checked (there, readable,
    its lines and sha256); else the whole <base>.jsonl as before. A missing or wrong segment, or a meta.json that
    cannot be read with no whole file beside it, is a problem: never passed off as the whole record. Files of its
    own names that meta.json does not list (left by a run stopped before it finished) do not change what the record
    is: they go into `notes` (a list, if given)."""
    notes = [] if notes is None else notes
    info = info or raw_info(folder, base)
    plain = os.path.join(folder, base + ".jsonl")
    if not info:
        stray = _unlisted(folder, base, set())
        for path, unzip in ((plain, False), (plain + ".gz", True)):
            if os.path.isfile(path):
                with open(path, "rb") as f:
                    data = f.read()
                if stray:
                    notes.append("meta.json 沒列的段檔：%s" % "、".join(stray))
                return (_gunzip(data) if unzip else data), []
        problems = [p for p in [_meta_problem(folder)] if p]
        if stray:
            problems.append("有段檔（%s）但 meta.json 沒有列出它們" % "、".join(stray))
        return b"", problems
    parts, problems = [], []
    for x in info["segments"]:
        path = os.path.join(folder, x.get("file", ""))
        try:
            with open(path, "rb") as f:
                data = f.read()
            data = _gunzip(data) if path.endswith(".gz") else data
        except FileNotFoundError:
            problems.append("缺了 %s" % x.get("file"))
            continue
        except (OSError, EOFError, ValueError, zlib.error) as e:
            problems.append("%s 讀不出來（%s）" % (x.get("file"), e))
            continue
        if x is info["segments"][-1] and not path.endswith(".gz") and _sha(data) != x.get("sha256") and \
                isinstance(x.get("bytes"), int) and _sha(data[:x["bytes"]]) == x.get("sha256"):
            data = data[:x["bytes"]]  # the open tail written further, meta.json not yet: what it names is still there
        if data.count(b"\n") != x.get("lines"):
            problems.append("%s 的行數跟 meta.json 記的不符（%d 行，記的是 %s 行）" % (x.get("file"), data.count(b"\n"), x.get("lines")))
        elif _sha(data) != x.get("sha256"):
            problems.append("%s 的內容跟 meta.json 記的雜湊不符（行數相同）" % x.get("file"))
        parts.append(data)
    extra = _unlisted(folder, base, {os.path.basename(x.get("file", "")) for x in info["segments"]})
    if extra:
        notes.append("meta.json 沒列的檔（中途停下的那一輪留下的，下次 export 會收掉）：%s" % "、".join(extra))
    whole = b"".join(parts)
    if not problems and _sha(whole) != info.get("sha256"):
        problems.append("各段串起來跟 meta.json 記的整份不符")
    return whole, problems


def load_raw(folder, base="raw"):
    """(records, [problem or note]) of a session folder's raw file, in segments or not (read_raw)."""
    notes = []
    data, problems = read_raw(folder, base, notes=notes)
    problems = problems + notes
    recs = []
    for line in data.decode("utf-8", "replace").split("\n"):
        line = line.strip()
        if line:
            try:
                recs.append(json.loads(line))
            except ValueError:
                pass
    return recs, problems


def store_raw(folder, base, recs, old):
    """Write a session's raw file: in segments when it already is (`old`, from meta.json) or when it is past
    SEG_MAX; else the whole <base>.jsonl. Returns (info for meta.json or None,
    problem or None). A source shorter than what is kept leaves every segment as it is (manager 2026-10-07)."""
    lines = raw_lines(recs)
    if old:
        if len(lines) < old.get("lines", 0):
            return old, "原始對話只剩 %d 行，比已存的 %d 行少：已存的段不動" % (len(lines), old.get("lines", 0))
        return write_segments(folder, base, lines, old), None
    if sum(len(l) for l in lines) > SEG_MAX:
        return write_segments(folder, base, lines), None
    write_raw(os.path.join(folder, base + ".jsonl"), recs)
    return None, None


def raw_link(info, rel):
    """The README's line for the raw file: one file, or its segments."""
    if not info:
        return "[%s](%s)" % (rel, rel)
    segs = info["segments"]
    top = os.path.dirname(rel)
    links = ["[%s](%s)" % (os.path.basename(x["file"]), os.path.join(top, os.path.basename(x["file"])) if top else x["file"])
             for x in segs]
    return "分 %d 段（`chatlog.py raw <資料夾>` 串回整份）：%s" % (len(segs), "、".join(links))


def split_existing(folder):
    """`chatlog.py split`: a raw.jsonl whose conversation is gone from this machine, put in segments from itself.
    The segments are written and checked to join into exactly the old file, meta.json is replaced, the old file is
    removed last; run again after being stopped anywhere, it picks up where it was. Returns a line to print."""
    plain = os.path.join(folder, "raw.jsonl")
    if unsure(folder):  # as export: what is kept is not known (#code-review 2026-10-08)
        return "不動：%s（有段檔但 meta.json 沒有完整的分段清單；見 docs/gotchas.md）" % folder
    info = raw_info(folder)
    if info:
        if not os.path.isfile(plain):
            return "已分段：%s" % folder
        whole, problems = read_raw(folder, info=info)
        with open(plain, "rb") as f:
            same = f.read() == whole
        if problems or not same:
            return "不動：%s（分段%s，舊的 raw.jsonl 也還在）" % (folder, "有問題：" + "；".join(problems) if problems else "跟舊檔不同")
        cleanup_segments(folder, "raw", info)
        return "收尾：%s（拿掉已被取代的 raw.jsonl）" % folder
    try:
        with open(os.path.join(folder, "meta.json"), encoding="utf-8") as f:
            meta = json.load(f)
        with open(plain, "rb") as f:
            data = f.read()
    except (OSError, ValueError) as e:
        return "不動：%s（%s）" % (folder, e)
    if not isinstance(meta, dict):
        return "不動：%s（meta.json 不是物件）" % folder
    if len(data) <= SEG_MAX:
        return "不用分：%s（%d bytes）" % (folder, len(data))
    if not data.endswith(b"\n"):
        return "不動：%s（最後一行沒有換行，可能還在寫）" % folder
    info = write_segments(folder, "raw", data.splitlines(True))
    whole, problems = read_raw(folder, info=info)
    if problems or whole != data:
        return "不動：%s（新的段串起來跟原檔不同：%s；原檔留著）" % (folder, "；".join(problems))
    meta["raw"] = info
    write(os.path.join(folder, "meta.json"), json.dumps(meta, ensure_ascii=False, indent=2) + "\n")
    cleanup_segments(folder, "raw", info)
    return "分好了：%s（%d 段）" % (folder, len(info["segments"]))


def render_session_md(meta, recs, out_dir, rel_raw, sub_links, raw=None):
    """Write README.md (+ part-NN.md if long) into out_dir. `raw`: its segments (meta.json's), if in segments."""
    for old in glob.glob(os.path.join(out_dir, "part-*.md")):
        os.remove(old)
    turns = [redact(t) for t in render_turns(recs)]
    head = "# %s\n\n%s\n" % (escape_tags(meta["title"]), meta_table(meta, [("原始紀錄", raw_link(raw, rel_raw))]))
    if sub_links:
        head += "\n## 子代理\n\n" + "\n".join(sub_links) + "\n"
    body = "\n".join(turns)
    if len(body.encode("utf-8")) <= MD_PART_MAX:
        write(os.path.join(out_dir, "README.md"), head + "\n## 對話\n\n" + body)
        return
    parts, cur, size = [], [], 0
    for t in turns:
        n = len(t.encode("utf-8"))
        if cur and size + n > MD_PART_MAX:
            parts.append(cur)
            cur, size = [], 0
        cur.append(t)
        size += n
    if cur:
        parts.append(cur)
    links = []
    for i, p in enumerate(parts, 1):
        name = "part-%02d.md" % i
        nav = "[← 回到總覽](README.md)"
        write(os.path.join(out_dir, name), "# %s（第 %d/%d 部分）\n\n%s\n\n%s\n\n%s\n" %
              (escape_tags(meta["title"]), i, len(parts), nav, "\n".join(p), nav))
        links.append("- [第 %d 部分](%s)" % (i, name))
    write(os.path.join(out_dir, "README.md"), head + "\n## 對話（共 %d 部分）\n\n%s\n" % (len(parts), "\n".join(links)))


def export_session(path):
    if not os.path.isdir(os.path.join(REPO, ".git")):
        # never create a stray folder (e.g. mid-migration, before the repo is cloned or moved into place)
        log("repo not found, skipping export: " + REPO)
        return None
    sid = os.path.splitext(os.path.basename(path))[0]
    recs = load_jsonl(path)
    meta = collect_meta(recs, path, sid)
    if not meta["start_utc"] or (meta["user_prompts"] == 0 and meta["assistant_messages"] == 0):
        return None
    out_dir = find_existing_dir(sid)
    if out_dir is None:  # its meta.json cannot be read: find the folder by name, to leave it alone if it is unsure
        named = glob.glob(os.path.join(REPO, "*", "*", "*_%s" % sid[:8]))
        out_dir = next((d for d in named if unsure(d)), None)
    old_meta = {}
    if out_dir and unsure(out_dir):
        if say_now("unsure " + out_dir):
            log("not exported, %s: 有自己檔名的段檔（%s）但 meta.json 沒有完整的分段清單；什麼都不動，等人看（docs/gotchas.md）"
                % (out_dir, "、".join(unsure(out_dir))))
        return None
    if out_dir:  # keep original host attribution / folder
        try:
            with open(os.path.join(out_dir, "meta.json")) as f:
                old_meta = json.load(f)
            old_meta = old_meta if isinstance(old_meta, dict) else {}  # not an object: written anew below
            meta["host"] = old_meta.get("host", meta["host"])
        except (OSError, ValueError):
            pass
        if raw_info(out_dir) and len(recs) < raw_info(out_dir).get("lines", 0):
            # the conversation here is shorter than what is kept (cut, or being rewritten): nothing of it is touched
            log("not exported, %s: 原始對話只有 %d 行，比已存的 %d 行少；已存的段和 README 都不動"
                % (out_dir, len(recs), raw_info(out_dir)["lines"]))
            return None
    else:
        start = parse_ts(meta["start_utc"]).astimezone(TZ)
        out_dir = os.path.join(REPO, HOST, start.strftime("%Y-%m"),
                               "%s_%s_%s" % (start.strftime("%Y-%m-%d_%H%M"), slugify(meta["project"]), sid[:8]))
    os.makedirs(out_dir, exist_ok=True)

    sub_links, subs_raw = [], {}
    sub_dir = os.path.join(os.path.dirname(path), sid, "subagents")
    for sp in sorted(glob.glob(os.path.join(sub_dir, "*.jsonl"))):
        aid = os.path.splitext(os.path.basename(sp))[0]
        srecs = load_jsonl(sp)
        smeta_extra = {}
        try:
            with open(sp[:-len(".jsonl")] + ".meta.json") as f:
                smeta_extra = json.load(f)
        except (OSError, ValueError):
            pass
        smeta_extra = smeta_extra if isinstance(smeta_extra, dict) else {}
        smeta = collect_meta(srecs, sp, aid)
        smeta["title"] = "子代理：%s" % (smeta_extra.get("description") or aid)
        rel = "subagents/%s" % aid
        info, problem = store_raw(out_dir, rel, srecs, raw_info(out_dir, rel))
        if info:
            subs_raw[aid] = info
        sdir = os.path.join(out_dir, rel)
        os.makedirs(sdir, exist_ok=True)
        if problem:
            log("subagent %s of %s: %s" % (aid, out_dir, problem))
        else:
            render_session_md(smeta, srecs, sdir, "../%s.jsonl" % aid, [], info)
        sub_links.append("- [%s](%s/)（%s）" % (smeta_extra.get("description") or aid, rel,
                                              smeta_extra.get("agentType", "")))
    meta["subagents"] = len(sub_links)

    info, _ = store_raw(out_dir, "raw", recs, raw_info(out_dir))  # shorter: returned before anything was written
    if info:
        meta["raw"] = info
    old_subs = old_meta.get("subagents_raw") if isinstance(old_meta, dict) else None
    for aid, sinfo in (old_subs if isinstance(old_subs, dict) else {}).items():
        if aid not in subs_raw and isinstance(sinfo, dict):  # its source gone this round: its segments stay listed
            subs_raw[aid] = sinfo
            log("subagent %s of %s: its source is gone; its segments are kept as they are" % (aid, out_dir))
    if subs_raw:
        meta["subagents_raw"] = subs_raw
    render_session_md(meta, recs, out_dir, "raw.jsonl", sub_links, info)
    write(os.path.join(out_dir, "meta.json"), json.dumps(meta, ensure_ascii=False, indent=2) + "\n")
    # meta.json now names the segments: only now is what they replace removed
    if info:
        cleanup_segments(out_dir, "raw", info)
    for aid, sinfo in subs_raw.items():
        if os.path.isfile(os.path.join(sub_dir, aid + ".jsonl")):  # only those written this round
            cleanup_segments(out_dir, "subagents/%s" % aid, sinfo)
    return out_dir


def _file_sig(path):
    """Size + hash of the last 4 KB: changes when content changes, not when only mtime is touched."""
    import hashlib
    size = os.path.getsize(path)
    with open(path, "rb") as f:
        f.seek(max(0, size - 4096))
        return "%d:%s" % (size, hashlib.md5(f.read()).hexdigest()[:12])


def transcript_sig(path):
    sig = [_file_sig(path)]
    sub = os.path.join(os.path.dirname(path), os.path.splitext(os.path.basename(path))[0], "subagents")
    for sp in sorted(glob.glob(os.path.join(sub, "*.jsonl"))):
        sig.append(_file_sig(sp))
    return sig


# ---------------------------------------------------------------- reconstruct (transcript already deleted)

RECON_NOTE = ("> 這個對話的完整紀錄已經被 Claude Code 自動清除（預設保留 30 天）。以下內容是用 `history.jsonl`"
              "（你輸入的每一句話）、貼上內容、背景任務紀錄，以及專案裡同一時段的 git commit 重建的。"
              "**沒有 AI 的完整回覆**，只有背景任務紀錄裡留下的片段。\n")


def ms_to_dt(ms):
    try:
        ms = float(ms)
        return datetime.fromtimestamp(ms / 1000 if ms > 1e12 else ms, timezone.utc)
    except (TypeError, ValueError):
        return None


def load_jobs():
    jobs = {}
    for sp in glob.glob(os.path.join(CLAUDE_DIR, "jobs", "*", "state.json")):
        try:
            with open(sp) as f:
                stj = json.load(f)
        except (OSError, ValueError):
            continue
        tl = []
        try:
            tl = load_jsonl(os.path.join(os.path.dirname(sp), "timeline.jsonl"))
        except OSError:
            pass
        for key in ("sessionId", "resumeSessionId"):
            if stj.get(key):
                jobs.setdefault(stj[key], (stj, tl))
    return jobs


def paste_text(pc):
    if not isinstance(pc, dict):
        return None
    if pc.get("type") == "image":
        return "[圖片]"
    if isinstance(pc.get("content"), str):
        return pc["content"]
    h = pc.get("contentHash")
    if h:
        try:
            with open(os.path.join(CLAUDE_DIR, "paste-cache", h + ".txt"), encoding="utf-8", errors="replace") as f:
                return f.read()
        except OSError:
            return None
    return None


_toplevel_cache = {}


def git_toplevel(path):
    if path not in _toplevel_cache:
        top = None
        if path and os.path.isdir(path):
            try:
                r = subprocess.run(["git", "-C", path, "rev-parse", "--show-toplevel"], stdout=subprocess.PIPE,
                                   stderr=subprocess.DEVNULL, universal_newlines=True, timeout=5)
                top = r.stdout.strip() if r.returncode == 0 else None
            except Exception:
                pass
        _toplevel_cache[path] = top
    return _toplevel_cache[path]


def commit_url_base(remote):
    m = re.match(r"(?:git@github\.com:|https://github\.com/)([^/]+/[^/]+?)(?:\.git)?$", remote or "")
    return "https://github.com/%s/commit/" % m.group(1) if m else None


def commits_between(project, start, end):
    """Commits in the project's repo during the session (a proxy for what the AI did)."""
    top = git_toplevel(project)
    if not top:
        return None, []
    since = (start - timedelta(minutes=10)).isoformat()
    until = (end + timedelta(hours=3)).isoformat()
    try:
        r = subprocess.run(["git", "-C", top, "log", "--all", "--since=" + since, "--until=" + until,
                            "--format=%H%x1f%ad%x1f%an%x1f%s%x1f%b%x1e", "--date=iso-strict", "--shortstat"],
                           stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, universal_newlines=True, timeout=20)
    except Exception:
        return top, []
    out = []
    for chunk in r.stdout.split("\x1e"):
        chunk = chunk.strip("\n")
        if not chunk.strip():
            continue
        # --shortstat of the previous commit lands at the start of the next chunk
        stat = ""
        lines = chunk.split("\n")
        while lines and "\x1f" not in lines[0]:
            if out and lines[0].strip():
                out[-1]["stat"] = lines[0].strip()
            lines.pop(0)
        if not lines:
            continue
        parts = "\n".join(lines).split("\x1f")
        if len(parts) < 5:
            continue
        h, date, author, subj, body = parts[:5]
        out.append({"hash": h, "date": date, "author": author, "subject": subj,
                    "claude": bool(re.search(r"Co-Authored-By:\s*Claude|Generated with \[Claude", body, re.I)),
                    "stat": stat})
    return top, out[:40]


def reconstruct_session(sid, items, jobs):
    items = sorted(items, key=lambda h: float(h.get("timestamp") or 0))
    times = [t for t in (ms_to_dt(h.get("timestamp")) for h in items) if t]
    if not times:
        return None
    start, end = times[0], times[-1]
    project = items[0].get("project") or ""
    job = jobs.get(sid)
    title = None
    if job and job[0].get("name"):
        title = job[0]["name"]
    if not title:
        for h in items:
            d = (h.get("display") or "").strip()
            if d and not d.startswith("/"):
                title = re.sub(r"\s+", " ", re.sub(r"\[Pasted text #\d+[^\]]*\]", "", d)).strip()[:60]
                if title:
                    break
    top, commits = commits_between(project, start, end)
    remote = git_info(top).get("remote") if top else None
    url = commit_url_base(remote)

    lines = []
    for h in items:
        ts = fmt_local(ms_to_dt(h.get("timestamp")))
        disp = h.get("display") or ""
        lines.append("### 使用者 · %s\n\n%s\n" % (ts, escape_tags(disp)))
        for pid, pc in sorted((h.get("pastedContents") or {}).items(), key=lambda kv: str(kv[0])):
            txt = paste_text(pc)
            label = "貼上內容 #%s" % pid
            lines.append(details(label, fence(clip(txt, 20000))) if txt else "*（%s 已不存在）*\n" % label)

    extra = []
    if job:
        stj, tl = job
        body = []
        if stj.get("intent"):
            body.append("**意圖**：" + stj["intent"])
        res = (stj.get("output") or {}).get("result") if isinstance(stj.get("output"), dict) else None
        if res:
            body.append("**最後結果**\n\n" + fence(clip(res, 5000)))
        for t in tl:
            seg = "**%s · %s**" % (fmt_local(parse_ts(t.get("at"))), t.get("state", ""))
            if t.get("detail"):
                seg += "\n\n" + escape_tags(clip(str(t["detail"]), 3000))
            if t.get("text"):
                seg += "\n\n" + details("AI 回覆片段", escape_tags(clip(str(t["text"]), 5000)))
            body.append(seg)
        extra.append("## 背景任務紀錄（含 AI 回覆片段）\n\n" + "\n\n".join(body) + "\n")
    if commits:
        rows = ["| 時間 | commit | 內容 | 變更 | Claude |", "|---|---|---|---|---|"]
        for c in commits:
            h = "[`%s`](%s%s)" % (c["hash"][:8], url, c["hash"]) if url else "`%s`" % c["hash"][:8]
            rows.append("| %s | %s | %s | %s | %s |" % (
                fmt_local(parse_ts(c["date"]))[5:16], h, escape_tags(c["subject"]).replace("|", "\\|"),
                c["stat"].replace("|", "\\|"), "是" if c["claude"] else ""))
        extra.append("## 同一時段的 git commit\n\n`%s` 在對話開始前 10 分鐘到結束後 3 小時之間的 commit，"
                     "可以看出 AI 實際做了什麼（不保證都來自這個對話）。\n\n%s\n" % (top, "\n".join(rows)))

    meta = {
        "session_id": sid, "title": redact(title or "(untitled)"), "reconstructed": True,
        "sources": ["history.jsonl"] + (["jobs"] if job else []) + (["git"] if commits else []),
        "host": HOST, "user": getpass.getuser(), "os": platform.platform(), "cwd": project,
        "project": os.path.basename(project.rstrip("/")) if project else "?",
        "git": {"remote": remote, "toplevel": top},
        "start_utc": start.isoformat(), "end_utc": end.isoformat(),
        "start_local": fmt_local(start), "end_local": fmt_local(end), "tz": TZ_LABEL,
        "duration_min": round((end - start).total_seconds() / 60, 1),
        "user_prompts": len(items), "assistant_messages": 0, "models": {},
        "git_commits": len(commits), "claude_commits": sum(1 for c in commits if c["claude"]),
    }
    table = "| 欄位 | 值 |\n|---|---|\n" + "\n".join("| %s | %s |" % (k, str(v).replace("|", "\\|")) for k, v in [
        ("主機", "%s（%s）" % (HOST, meta["os"])), ("使用者", meta["user"]), ("工作目錄", "`%s`" % project),
        ("Git", remote or top or "—"),
        ("時間", "%s → %s（%s，約 %s 分鐘，依輸入時間推算）" % (meta["start_local"], meta["end_local"], TZ_LABEL,
                                                    meta["duration_min"])),
        ("使用者訊息", len(items)), ("同時段 commit", "%d（其中 Claude 共同作者 %d）" % (len(commits), meta["claude_commits"])),
        ("資料來源", ", ".join(meta["sources"])), ("Session", "`%s`" % sid)])

    out_dir = os.path.join(REPO, HOST, start.astimezone(TZ).strftime("%Y-%m"), "%s_%s_%s" % (
        start.astimezone(TZ).strftime("%Y-%m-%d_%H%M"), slugify(meta["project"]), sid[:8]))
    md = "# %s（重建）\n\n%s\n%s\n%s\n## 對話（只有使用者訊息）\n\n%s\n" % (
        escape_tags(meta["title"]), RECON_NOTE, table, "\n" + "\n".join(extra) if extra else "", "\n".join(lines))
    write(os.path.join(out_dir, "README.md"), redact(md))
    write(os.path.join(out_dir, "meta.json"), json.dumps(meta, ensure_ascii=False, indent=2) + "\n")
    write(os.path.join(out_dir, "history.jsonl"),
          "\n".join(redact(json.dumps(h, ensure_ascii=False)) for h in items) + "\n")
    return out_dir


def existing_sessions():
    idx = {}
    for mp in glob.glob(os.path.join(REPO, "*", "*", "*", "meta.json")):
        try:
            with open(mp) as f:
                m = json.load(f)
            if isinstance(m, dict):  # a broken one is left out (its folder is said by write_host_index)
                idx[m.get("session_id")] = m
        except (OSError, ValueError):
            pass
    return idx


def reconstruct_all(st, full=False, failed=None):
    """Sessions rebuilt from history.jsonl; each one that fails is logged and added to `failed` (sid, error)."""
    hp = os.path.join(CLAUDE_DIR, "history.jsonl")
    if not os.path.exists(hp):
        return 0
    groups = {}
    for h in load_jsonl(hp):
        if h.get("sessionId"):
            groups.setdefault(h["sessionId"], []).append(h)
    have = set(os.path.splitext(os.path.basename(p))[0]
               for p in glob.glob(os.path.join(CLAUDE_DIR, "projects", "*", "*.jsonl")))
    done = st.setdefault("recon", {})
    existing = existing_sessions()
    jobs = load_jobs()
    n = 0
    for sid, items in groups.items():
        if sid in have:
            continue
        m = existing.get(sid)
        if m and not m.get("reconstructed"):
            continue  # a full export already exists; never overwrite it
        if not full and done.get(sid) == len(items) and m:
            continue
        try:
            if reconstruct_session(sid, items, jobs):
                n += 1
            done[sid] = len(items)
        except Exception as e:
            log("reconstruct failed %s: %r" % (sid, e))
            if failed is not None:
                failed.append((sid, e))
    return n


# ---------------------------------------------------------------- repo sync

REPO_README = """# altention-logs

所有 Claude Code 對話紀錄，由 Altention 的 chatlog 自動同步。

- 每台機器一個資料夾：`<主機>/<年-月>/<日期_時間>_<專案>_<session>/`
- 每個 session 資料夾：`README.md`（可直接在 GitHub 閱讀）、`raw.jsonl`（原始紀錄，超長欄位已截斷；超過 8 MB 的分成 `raw-0001.jsonl.gz`… 加最後一段 `raw-000N.jsonl`，`meta.json` 列出各段，`chatlog.py raw <資料夾>` 串回整份）、`meta.json`、`subagents/`
- 各主機資料夾的 `README.md` 是依時間排序的索引
- 時間一律為 %s
""" % TZ_LABEL


def _index_row(m, rel):
    """(sort key, month, the index line) of one session's meta.json (as it always was written)."""
    title = (m.get("title") or "").replace("|", "\\|").replace("[", "\\[").replace("]", "\\]")
    if m.get("reconstructed"):
        title += "（重建）"
    line = "| %s | %s | [%s](%s/) | %s | %s |" % (
        (m.get("start_local") or "")[5:16], m.get("project", ""), title or "(untitled)", rel,
        m.get("user_prompts", 0), ", ".join(k.replace("claude-", "") for k in (m.get("models") or {})))
    return str(m.get("start_utc") or ""), (m.get("start_local") or "?")[:7], line


def write_host_index():
    """This machine's README.md, one line per session. Each folder on its own: a meta.json that cannot be read or
    made into its line, whatever the reason, is left out and said (an hour apart); the sync goes on for every other
    one (manager 2026-10-08)."""
    host_dir = os.path.join(REPO, HOST)
    rows = []
    for mp in glob.glob(os.path.join(host_dir, "*", "*", "meta.json")):
        try:
            with open(mp) as f:
                m = json.load(f)
            if not isinstance(m, dict):
                raise ValueError("不是物件")
            rows.append(_index_row(m, os.path.relpath(os.path.dirname(mp), host_dir)))
        except Exception as e:  # left out of the index and said; the sync goes on for every other one
            if say_now("index " + mp):
                log("left out of the index, %s: meta.json 讀不出來或欄位不對（%s）" % (os.path.dirname(mp), e))
    rows.sort(key=lambda r: r[0], reverse=True)
    lines = ["# %s 的對話紀錄\n" % HOST, "共 %d 個 session，時間為 %s。\n" % (len(rows), TZ_LABEL)]
    month = None
    for _, mo, line in rows:
        if mo != month:
            month = mo
            lines.append("\n## %s\n\n| 開始 | 專案 | 標題 | 訊息 | 模型 |\n|---|---|---|---|---|" % mo)
        lines.append(line)
    write(os.path.join(host_dir, "README.md"), "\n".join(lines) + "\n")


def do_sync(full=False, only=None):
    st = load_state()
    if not os.path.isdir(os.path.join(REPO, ".git")):
        log("repo not found: " + REPO)
        return 1
    if git("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}").returncode == 0:  # no remote: fine, local only
        git("pull", "--rebase", "--autostash", "-q")
    paths = [only] if only else glob.glob(os.path.join(CLAUDE_DIR, "projects", "*", "*.jsonl"))
    n = 0
    tried, failed = 0, []  # every one tried failing (e.g. the redaction does not load) is said, not "sync ok"
    for p in paths:
        try:
            sig = transcript_sig(p)
            if not full and st["sigs"].get(p) == sig:
                continue
            tried += 1
            if export_session(p):
                n += 1
            st["sigs"][p] = sig
        except Exception as e:  # keep going on one bad transcript
            log("export failed %s: %r" % (p, e))
            failed.append((p, e))
    if not only:  # the engine and the instance are kept in step by altention/cron_sync.py, not here (3a.10)
        hp = os.path.join(CLAUDE_DIR, "history.jsonl")
        hsig = [os.path.getsize(hp), int(os.path.getmtime(hp))] if os.path.exists(hp) else None
        if full or st.get("history_sig") != hsig:
            before = len(failed)
            r = reconstruct_all(st, full, failed)
            tried += r + len(failed) - before
            if r:
                log("reconstructed %d session(s) from history" % r)
                n += r
            st["history_sig"] = hsig
    if not os.path.exists(os.path.join(REPO, "README.md")):
        write(os.path.join(REPO, "README.md"), REPO_README)
    if not os.path.exists(os.path.join(REPO, ".gitattributes")):
        write(os.path.join(REPO, ".gitattributes"), "*.jsonl -diff linguist-generated\n")
    if os.path.isdir(os.path.join(REPO, HOST)):
        write_host_index()
    git("add", "-A", "--", ".")
    changed = set()
    if git("diff", "--cached", "--quiet").returncode != 0:
        for f in git("diff", "--cached", "--name-only").stdout.split("\n"):
            parts = f.split("/")
            if len(parts) >= 4:
                changed.add("/".join(parts[:3]))
        r = git("commit", "-q", "-m", "%s: %d session(s) updated @ %s" % (
            HOST, len(changed), datetime.now(TZ).strftime("%Y-%m-%d %H:%M")))
        if r.returncode != 0:
            st["last_error"] = "commit: " + r.stderr.strip()[-300:]  # the end: the reason, not the warnings before it
            log(st["last_error"])
            save_state(st)
            return 1
    ahead = git("rev-list", "--count", "@{u}..HEAD").stdout.strip()
    has_origin = "origin" in git("remote").stdout.split()  # none: a machine that keeps its logs locally, which is fine
    if has_origin and (ahead not in ("", "0") or git("rev-parse", "@{u}").returncode != 0):
        r = git("push", "-q", "-u", "origin", "HEAD", timeout=600)
        if r.returncode != 0:
            for attempt in range(3):  # other hosts may push at the same moment
                time.sleep(5 + attempt * 10)
                git("pull", "--rebase", "-q")
                r = git("push", "-q", "-u", "origin", "HEAD", timeout=600)
                if r.returncode == 0:
                    break
        if r.returncode != 0:
            st["last_error"] = "push: " + r.stderr.strip()[-300:]  # the end: the reason (LFS warnings come first)
            log(st["last_error"])
            save_state(st)
            return 1
    st["last_sync"] = datetime.now(TZ).isoformat(timespec="seconds")
    if not only and not full:
        # The plain `chatlog.py sync`: what the schedule runs (plugin.json; a session's end goes through `export
        # --sync <path>`, a rebuild through `sync --all`). The schedule's trace (do.trace) is this one: last_sync
        # is brought up to date by every session that ends, and the scheduler's --compare took those for runs at
        # other times than the schedule's (manager 2026-10-09)
        st["last_cron_sync"] = st["last_sync"]
    if tried and len(failed) == tried:
        st["last_error"] = ("export: all %d failed, e.g. %r" % (tried, failed[0][1]))[:300]
        save_state(st)
        log("sync done, but " + st["last_error"])
        return 1
    st["last_error"] = None
    save_state(st)
    log("sync ok, %d session(s) rendered, %d changed" % (n, len(changed)))
    return 0


# ---------------------------------------------------------------- CLI

REPORT_MARKER = "<!-- progress-report -->"


def last_assistant_has_report(path):
    """True if the newest assistant text in the transcript is a /report (so push right away)."""
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            f.seek(max(0, size - 262144))
            lines = f.read().decode("utf-8", "replace").splitlines()
    except OSError:
        return False
    for line in reversed(lines):
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if r.get("type") == "user" and user_text_of(r):
            return False
        if r.get("type") == "assistant":
            for b in (r.get("message") or {}).get("content") or []:
                if isinstance(b, dict) and b.get("type") == "text" and REPORT_MARKER in b.get("text", ""):
                    return True
    return False


def background_claude():
    """Is this hook running in a background Claude (altention/background.py: one a part of the engine started for a
    job of its own)? The engine's shared code lies beside this plugin's folder; where it cannot be had (not there,
    or a file of it that does not load), nothing is taken for one, and the hook goes on as for any conversation: every
    Claude on the machine runs this, and it must not fail for what is not its own."""
    try:
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        from altention import background
        return background.marked()
    except Exception:
        return False


def cmd_hook():
    if background_claude():
        return 0
    try:
        data = json.load(sys.stdin)
    except ValueError:
        return 0
    path = data.get("transcript_path")
    if not path or not os.path.exists(path):
        return 0
    args = [sys.executable, os.path.abspath(__file__), "export", path]
    if data.get("hook_event_name") == "SessionEnd" or last_assistant_has_report(path):
        args.append("--sync")
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(os.path.join(STATE_DIR, "chatlog.log"), "a") as lf:
        subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=lf, stderr=lf,
                         start_new_session=True, close_fds=True)
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("hook")
    e = sub.add_parser("export")
    e.add_argument("path")
    e.add_argument("--sync", action="store_true")
    s = sub.add_parser("sync")
    s.add_argument("--all", action="store_true", help="re-export every transcript")
    sub.add_parser("status")
    r = sub.add_parser("raw", help="a session folder's raw file, its segments joined, to stdout")
    r.add_argument("folder")
    r.add_argument("--sub", metavar="AGENT", help="a subagent's raw file (subagents/AGENT) instead of the main one")
    sp = sub.add_parser("split", help="put big raw.jsonl files of this machine in segments (their conversation gone)")
    sp.add_argument("folders", nargs="*")
    sp.add_argument("--over", type=float, default=30, help="MB: without folders, this machine's raw.jsonl past this")
    a = ap.parse_args()

    if a.cmd == "hook":
        return cmd_hook()
    if a.cmd == "export":
        with Lock():
            if a.sync:
                return do_sync(only=a.path)
            st = load_state()
            try:
                export_session(a.path)
                st["sigs"][a.path] = transcript_sig(a.path)
                save_state(st)
            except Exception as ex:
                log("export failed %s: %r" % (a.path, ex))
                return 1
        return 0
    if a.cmd == "sync":
        with Lock():
            return do_sync(full=a.all)
    if a.cmd == "raw":
        notes = []
        data, problems = read_raw(os.path.abspath(a.folder), "subagents/%s" % a.sub if a.sub else "raw", notes=notes)
        if not problems and not data:
            problems = ["沒有原始紀錄（%s）" % a.folder]
        for n in notes:
            sys.stderr.write("chatlog raw：%s：%s\n" % (a.folder, n))
        if problems:
            sys.stderr.write("chatlog raw：%s：%s\n" % (a.folder, "；".join(problems)))
            return 1
        try:
            sys.stdout.buffer.write(data)
            sys.stdout.buffer.flush()
        except BrokenPipeError:  # the reader went away (`… | head`): nothing to say
            os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())  # or the exit's own flush complains
        return 0
    if a.cmd == "split":
        folders = [os.path.abspath(f) for f in a.folders] or sorted(
            os.path.dirname(p) for p in glob.glob(os.path.join(REPO, HOST, "*", "*", "raw.jsonl"))
            if os.path.getsize(p) > a.over * 1024 * 1024)
        with Lock():
            for f in folders:
                print(split_existing(f))
                log("split: " + f)
        return 0
    if a.cmd == "status":
        st = load_state()
        print("host:        %s\nrepo:        %s\nlast sync:   %s\nlast error:  %s\ntracked:     %d transcripts" % (
            HOST, REPO, st.get("last_sync"), st.get("last_error"), len(st.get("sigs", {}))))
        return 0
    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
