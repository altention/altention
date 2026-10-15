"""Where Claude Code keeps its conversations and its record of what is running, read into plain data: the config
folder (~/.claude, or CLAUDE_CONFIG_DIR), a working directory's conversations (projects/<cwd with every
non-alphanumeric as "-">/<session id>.jsonl), the records of its running processes (sessions/<pid>.json), which
conversation a tmux pane shows, and the last turns of a conversation. Nothing here writes, and nothing here words
anything for the owner. From the bot's sessions.py, peers.py and tmuxio.py as they were (stage 3b step 1.7); turns()
and turn_start() give kinds where the bot's recap() gave its own words, which stay there."""
import glob
import json
import os
import re
import subprocess

from .transcript import content_of, is_text, part, text_of


def config_dir():
    return os.path.expanduser(os.environ.get("CLAUDE_CONFIG_DIR") or "~/.claude")


def project_dir(cwd):
    return os.path.join(config_dir(), "projects", re.sub(r"[^A-Za-z0-9]", "-", cwd))


def _prompt(d):
    """The text of a transcript line if it is something the user typed, else None."""
    content = content_of(d)  # (a row whose message is no object, a block whose text is no string: passed over)
    if isinstance(content, list):
        content = " ".join(text_of(b) for b in content if is_text(b))
    if not isinstance(content, str) or not content.strip() or d.get("isMeta") or d.get("isSidechain"):
        return None
    if isinstance(d.get("origin"), dict):
        return content.strip() if d["origin"].get("kind") == "human" else None
    return None if content.lstrip()[:1] in ("<", "[") else content.strip()  # older transcripts: no origin field


def summarize(path):
    """{"id", "mtime", "title", "first", "last", "prompts"} for one transcript, or None if nobody typed in it."""
    title, first, last, n = "", "", "", 0
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                if '"type":"user"' not in line and '"type":"ai-title"' not in line:
                    continue
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(d, dict):
                    continue
                if d.get("type") == "ai-title":
                    title = d["aiTitle"] if isinstance(d.get("aiTitle"), str) and d["aiTitle"] else title
                elif d.get("type") == "user":
                    text = _prompt(d)
                    if text:
                        first, last, n = first or text, text, n + 1
        mtime = os.path.getmtime(path)
    except OSError:
        return None
    if not n:
        return None
    return {"id": os.path.basename(path)[:-len(".jsonl")], "mtime": mtime, "title": title, "first": first,
            "last": last, "prompts": n}


def recent(cwd, limit=8):
    """The directory's latest conversations, newest first."""
    d = project_dir(cwd)
    try:
        paths = [os.path.join(d, n) for n in os.listdir(d) if n.endswith(".jsonl")]
    except OSError:
        return []
    out = []
    for p in sorted(paths, key=lambda p: -os.path.getmtime(p) if os.path.exists(p) else 0):
        s = summarize(p)
        if s:
            out.append(s)
        if len(out) >= limit:
            break
    return out


def find(cwd, ref):
    """A conversation of the directory by the start of its id (at least 8 characters), however old."""
    if len(ref) < 8:
        return None
    d = project_dir(cwd)
    try:
        hits = [n for n in os.listdir(d) if n.endswith(".jsonl") and n.startswith(ref.lower())]
    except OSError:
        return None
    return summarize(os.path.join(d, hits[0])) if len(hits) == 1 else None


def pid_alive(pid):
    """Is there a process with this id (Linux or macOS)?"""
    try:
        os.kill(int(pid), 0)
    except PermissionError:
        return True
    except (OSError, TypeError, ValueError):
        return False
    return True


def transcript(cwd, session_id):
    return os.path.join(project_dir(cwd), session_id + ".jsonl")


def of_pane(pane_id, tmux_session=None):
    """The running Claude Code in a tmux pane, from the records Claude Code keeps of its own processes
    (~/.claude/sessions/<pid>.json, whose "tmux" field reads "<session>:@<window>.<pane>"):
    {"sessionId", "cwd", …}, or None."""
    d, best = os.path.join(config_dir(), "sessions"), None
    try:
        names = [n for n in os.listdir(d) if n.endswith(".json")]
    except OSError:
        return None
    for n in names:
        try:
            with open(os.path.join(d, n)) as f:
                rec = json.load(f)
        except (OSError, ValueError):
            continue
        where = str(rec.get("tmux") or "")
        if not (where.endswith("." + pane_id) and rec.get("sessionId")) or \
                (tmux_session and not where.startswith(tmux_session + ":")):
            continue
        if not pid_alive(rec.get("pid")):
            continue  # a record left behind by a process that is gone
        if best is None or rec.get("startedAt", 0) > best.get("startedAt", 0):
            best = rec
    return best


def live_session(cwd, screen, rec=None, tail_bytes=3 * 1024 * 1024):
    """The session id a running Claude Code writes to now. Its process record keeps the id it started with,
    but a conversation can move to another transcript later (a /resume, a continued conversation). The
    transcript whose end holds the most of the lines now on the pane's screen is the live one."""
    folder = project_dir(cwd)
    started = ((rec or {}).get("startedAt") or 0) / 1000.0
    wanted = [row.strip() for row in screen.split("\n") if len(row.strip()) >= 20]
    best, best_score, best_mtime = None, 0, 0
    try:
        names = [n for n in os.listdir(folder) if n.endswith(".jsonl")]
    except OSError:
        names = []
    for n in names:
        path = os.path.join(folder, n)
        try:
            mtime = os.path.getmtime(path)
            if mtime < started - 60:
                continue  # last written before this process started
            with open(path, "rb") as f:
                f.seek(0, os.SEEK_END)
                f.seek(max(0, f.tell() - tail_bytes))
                tail = f.read().decode("utf-8", "replace")
        except OSError:
            continue
        score = sum(1 for row in wanted if row in tail)
        if score > best_score or (score and score == best_score and mtime > best_mtime):
            best, best_score, best_mtime = n[:-len(".jsonl")], score, mtime
    return best if best_score else (rec or {}).get("sessionId")


def process_records():
    """What Claude Code keeps of its own running processes (<config>/sessions/<pid>.json), every record that can be
    read, whether its process is still there or not: [{"pid", "sessionId", "cwd", "tmux", "name", …}]."""
    d, out = os.path.join(config_dir(), "sessions"), []
    try:
        names = [n for n in os.listdir(d) if n.endswith(".json")]
    except OSError:
        return []
    for n in names:
        try:
            with open(os.path.join(d, n)) as f:
                rec = json.load(f)
        except (OSError, ValueError):
            continue
        if isinstance(rec, dict):
            out.append(rec)
    return out


def parked(session_id):
    """The records of the Claude Code processes whose conversation was moved to a background process and is this one
    now. Pressing the left key on an empty input box (the agents list) does that: the conversation goes on under a
    new id in a process of Claude Code's daemon, whose hooks see no pane, and nothing in its events says where it
    came from. What does: the record of the process still in the pane gets "parkedJobId", the first eight
    characters of the new id, 0.35 to 0.5 s before the new process's SessionStart (measured on 2.1.288 and 2.1.296,
    ten runs), and keeps it whatever the pane looks at afterwards. Only that conversation is told this way: one
    opened by typing into the agents list is tied to no pane by anything Claude Code writes. Records of processes
    that are gone, and of background processes themselves, are left out."""
    ref = str(session_id or "")[:8]
    if len(ref) < 8:
        return []
    return [rec for rec in process_records()
            if rec.get("parkedJobId") == ref and rec.get("kind") != "bg" and pid_alive(rec.get("pid"))]


def attached_session(pid):
    """The session id a pane shows when its process is `claude attach <id>`: a viewer of a background session
    (Claude Code's agents view). That session runs under Claude Code's daemon, not in the pane, so its hooks see
    neither the pane nor its process; the route table matches it by this id instead. None for anything else."""
    try:
        with open("/proc/%s/cmdline" % pid, "rb") as f:
            cmd = f.read().replace(b"\0", b" ").decode("utf-8", "replace")
    except (OSError, ValueError):
        cmd = subprocess.run(["ps", "-o", "command=", "-p", str(pid)], stdout=subprocess.PIPE,
                             stderr=subprocess.DEVNULL, universal_newlines=True).stdout if str(pid).isdigit() else ""
    m = re.search(r"\battach\s+([0-9a-f][0-9a-f-]{5,})", cmd)
    if not m:
        return None
    ref = m.group(1)
    for path in glob.glob(os.path.join(config_dir(), "sessions", "*.json")):
        try:
            with open(path) as f:
                sid = (json.load(f) or {}).get("sessionId") or ""
        except (OSError, ValueError):
            continue
        if sid.startswith(ref):
            return sid
    return ref


def turn_start(d, seen_tasks):
    """What opened a turn, if this transcript line is one, as (kind, text): ("asked", what the owner typed),
    ("midway", a message sent while Claude was busy, recorded as a queued command), or ("task", the summary of a
    background task reporting back, None when it gave none: Claude's reply to it is often the point of a
    long-running session). None for anything else."""
    text = _prompt(d) if d.get("type") == "user" else None
    if text:
        return ("asked", text)
    raw = None
    if d.get("type") == "user" and part(d, "origin").get("kind") == "task-notification":
        raw = content_of(d)
    elif d.get("type") == "attachment" and part(d, "attachment").get("type") == "queued_command":
        a = part(d, "attachment")
        raw = a.get("prompt") if isinstance(a.get("prompt"), str) else ""
        if a.get("commandMode") != "task-notification" and not raw.lstrip().startswith("<task-notification"):
            return ("midway", raw.strip()) if raw.strip() and not raw.lstrip().startswith("<") else None
    if not isinstance(raw, str) or "<task-notification" not in raw:
        return None
    task = _between(raw, "<task-id>", "</task-id>", lines=False)
    if task is not None and task in seen_tasks:
        return None  # the same notification, queued first and delivered later
    if task is not None:
        seen_tasks.add(task)
    summary = _between(raw, "<summary>", "</summary>")
    return ("task", " ".join(summary.split()) if summary is not None else None)


def _between(text, opening, closing, lines=True):
    """What lies between the first `opening` and the `closing` that follows it, or None. `lines` False: not across a
    line's end (then the next `opening` is tried). One pass over the text: the patterns these were ("<x>(.*?)</x>")
    took the square of the length for many openings and no closing."""
    at = text.find(opening)
    while at >= 0:
        start = at + len(opening)
        stop = text.find(closing, start)
        if stop < 0:
            return None  # none after this opening: none after a later one either
        if lines or "\n" not in text[start:stop]:
            return text[start:stop]
        at = text.find(opening, text.rfind("\n", start, stop) + 1)  # within one line: an opening past that line's end
    return None


def turns(path, last=3, tail_bytes=4 * 1024 * 1024):
    """The last `last` exchanges of a transcript, oldest first, each [kind, text, Claude's last message of that turn,
    the time it began] (kind and text as turn_start() gives them). Only the end of the file is read (a long
    conversation's transcript is many megabytes), unless `last` and `tail_bytes` are None: then it is the whole
    conversation."""
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - tail_bytes) if tail_bytes else 0)
            data = f.read()
    except OSError:
        return []
    lines = data.split(b"\n")
    if tail_bytes and size > tail_bytes:
        lines = lines[1:]  # the first line is cut somewhere in the middle
    out, seen_tasks = [], set()
    for line in lines:
        if b'"type":"user"' not in line and b'"type":"assistant"' not in line and b'"queued_command"' not in line:
            continue
        try:
            d = json.loads(line.decode("utf-8", "replace"))
        except ValueError:
            continue
        if not isinstance(d, dict) or d.get("isSidechain"):
            continue
        start = turn_start(d, seen_tasks)  # what the owner typed, or a background task that finished
        if start:
            out.append([start[0], start[1], "", d.get("timestamp") or part(d, "attachment").get("timestamp") or ""])
        elif d.get("type") == "assistant" and out:
            content = content_of(d)
            for b in content if isinstance(content, list) else []:
                if text_of(b).strip():
                    out[-1][2] = text_of(b).strip()
    return out if last is None else out[-last:]
