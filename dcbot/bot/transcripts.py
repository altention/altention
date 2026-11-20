"""A Claude Code conversation's record carried to another folder or machine (C-201 2c2, /move machine and /move
folder; plan approved by the manager 11-20).

The record lies under the HOME of the machine, by the folder it was opened in: `~/.claude/projects/<folder with
every character but a letter or digit turned into "-">/<id>.jsonl`, and next to it a folder `<id>/` (subagents'
records, tool results; it may hold folders of its own). Both are packed here as a tar (gzip, in base64: one line of
text, as remote.run() carries it), read where the record is and unpacked where it goes, by dcremote over there or in
the bot's own process here (the same functions).

Unpacking: every member checked (only `<id>.jsonl` and what lies under `<id>/`, plain files and folders; no link, no
"..", no absolute path), written into a temporary folder next to the place first, each file's size compared with
the tar's, then renamed into place. The record the conversation leaves behind is never removed (the old copy kept:
the owner's choice 4, 11-17). One already there with the same id: the one being carried normally began as it (the
conversation moved away from there before), and then it is replaced; one that does not begin so is kept first as
`<id>.jsonl.before-move-<time>` (its folder as `<id>.before-move-<time>`), and said."""
import base64
import io
import os
import re
import shutil
import tarfile
import time

ID = re.compile(r"^[0-9A-Za-z][0-9A-Za-z_-]{0,99}$")  # a conversation id (a uuid), nothing a path could turn on


class Refused(Exception):
    """The record cannot be carried: why, for the card."""


def encoded(folder):
    """Claude Code's name for a folder's records (bot/where.py transcript() reads it the same way)."""
    return re.sub(r"[^A-Za-z0-9]", "-", folder)


def project_dir(folder, home=None):
    return os.path.join(home or os.path.expanduser("~"), ".claude", "projects", encoded(folder))


def check_id(sid):
    if not isinstance(sid, str) or not ID.match(sid):
        raise Refused("對話 id 不對（%r）" % (sid,))


def pack(folder, sid, home=None):
    """The record of conversation `sid` opened in `folder`, as one line of base64 (gzip tar: `<sid>.jsonl`, and
    `<sid>/` with all it holds when there is one). Refused when there is no record."""
    check_id(sid)
    base = project_dir(folder, home)
    record = os.path.join(base, sid + ".jsonl")
    if not os.path.isfile(record):
        raise Refused("找不到這個對話的紀錄（%s）" % record)
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz", compresslevel=3) as tar:  # (a record of hundreds of MB: speed first)
        tar.add(record, arcname=sid + ".jsonl", recursive=False)
        side = os.path.join(base, sid)
        if os.path.isdir(side) and not os.path.islink(side):
            for root, dirs, files in os.walk(side):
                dirs[:] = sorted(d for d in dirs if not os.path.islink(os.path.join(root, d)))
                rel = os.path.relpath(root, base)
                tar.add(root, arcname=rel, recursive=False)
                for name in sorted(files):
                    path = os.path.join(root, name)
                    if os.path.isfile(path) and not os.path.islink(path):
                        tar.add(path, arcname=os.path.join(rel, name), recursive=False)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def size(folder, sid, home=None):
    """Bytes of what pack() would carry (the record and its folder's plain files, no link followed). Refused when
    there is no record."""
    check_id(sid)
    base = project_dir(folder, home)
    record = os.path.join(base, sid + ".jsonl")
    if not os.path.isfile(record):
        raise Refused("找不到這個對話的紀錄（%s）" % record)
    total = os.path.getsize(record)
    side = os.path.join(base, sid)
    if os.path.isdir(side) and not os.path.islink(side):
        for root, dirs, files in os.walk(side):
            dirs[:] = [d for d in dirs if not os.path.islink(os.path.join(root, d))]
            for name in files:
                path = os.path.join(root, name)
                if os.path.isfile(path) and not os.path.islink(path):
                    total += os.path.getsize(path)
    return total


def mem_available():
    """MemAvailable of this machine in bytes, None when not known (not Linux)."""
    try:
        with open("/proc/meminfo", encoding="utf-8") as f:
            for line in f:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) * 1024
    except (OSError, ValueError, IndexError):
        pass
    return None


def members(tar, sid):
    """The tar's members, every one checked (refused at the first that is not ours)."""
    out = []
    for m in tar.getmembers():
        name = m.name
        parts = name.split("/")
        if name.startswith("/") or ".." in parts or "" in parts[1:] or not (name == sid + ".jsonl" or parts[0] == sid):
            raise Refused("紀錄裡有不該有的檔案：%r" % name)
        if name == sid + ".jsonl" and not m.isfile():
            raise Refused("紀錄的 %s 不是一般檔案" % name)
        if not (m.isfile() or m.isdir()):
            raise Refused("紀錄裡有連結或特殊檔案：%r" % name)
        out.append(m)
    if not any(m.name == sid + ".jsonl" for m in out):
        raise Refused("紀錄裡沒有 %s.jsonl" % sid)
    return out


def begins(path, new_path):
    """Does the file at `new_path` begin with all of the one at `path`?"""
    size = os.path.getsize(path)
    if os.path.getsize(new_path) < size:
        return False
    with open(path, "rb") as a, open(new_path, "rb") as b:
        while True:
            chunk = a.read(1 << 20)
            if not chunk:
                return True
            if b.read(len(chunk)) != chunk:
                return False


def unpack(data, folder, sid, home=None, now=None):
    """Write the record pack() made (`data`) as conversation `sid`'s in `folder`. {"files": how many, "kept": the
    name an earlier, different record of the same id was kept under, or None}. Refused (nothing written into place)
    when the data is not such a record or a size does not match."""
    check_id(sid)
    try:
        raw = base64.b64decode((data or "").strip(), validate=True)
        tar = tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz")
    except Exception as e:
        raise Refused("紀錄讀不出來（%s）" % type(e).__name__)
    base = project_dir(folder, home)
    os.makedirs(base, mode=0o700, exist_ok=True)
    tmp = os.path.join(base, ".move-%s-%d" % (sid, os.getpid()))
    shutil.rmtree(tmp, ignore_errors=True)
    os.makedirs(tmp, mode=0o700)
    try:
        with tar:
            got = members(tar, sid)
            for m in got:
                target = os.path.join(tmp, m.name)
                if m.isdir():
                    os.makedirs(target, mode=0o700, exist_ok=True)
                    continue
                os.makedirs(os.path.dirname(target), mode=0o700, exist_ok=True)
                src = tar.extractfile(m)
                with open(os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "wb") as f:
                    shutil.copyfileobj(src, f)  # (0600 as Claude Code writes its records)
                if os.path.getsize(target) != m.size:
                    raise Refused("%s 的大小不對（%d，應該是 %d）" % (m.name, os.path.getsize(target), m.size))
        files = sum(1 for m in got if m.isfile())
        record, new_record = os.path.join(base, sid + ".jsonl"), os.path.join(tmp, sid + ".jsonl")
        kept = None
        if os.path.exists(record) and not begins(record, new_record):
            stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(now))
            kept = "%s.jsonl.before-move-%s" % (sid, stamp)
            os.replace(record, os.path.join(base, kept))
            side = os.path.join(base, sid)
            if os.path.isdir(side):
                os.replace(side, os.path.join(base, "%s.before-move-%s" % (sid, stamp)))
        side_new = os.path.join(tmp, sid)
        if os.path.isdir(side_new):  # each file put in place (a file there already that is not carried stays)
            for root, dirs, names in os.walk(side_new):
                rel = os.path.relpath(root, tmp)
                os.makedirs(os.path.join(base, rel), mode=0o700, exist_ok=True)
                for name in names:
                    os.replace(os.path.join(root, name), os.path.join(base, rel, name))
        os.replace(new_record, record)  # (the record last: there, it is all there)
        return {"files": files, "kept": kept}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def probe(pane_pid):
    """What a move needs to know of a pane's processes on this machine, for dcremote `move-probe` (the bot asks its
    own process table the same way): {"shell": the pane's process is a shell, "argv": the words of the Claude under
    it or None, "daemon": a `claude daemon` of this user runs here}."""
    from altention.connectors.claude_code import process as cc_process
    shells = ("bash", "zsh", "fish", "sh", "dash", "ksh", "tcsh", "csh")
    try:
        root = int(pane_pid)
    except (TypeError, ValueError):
        root = None
    shell = False
    if root is not None:
        try:
            with open("/proc/%d/comm" % root, encoding="utf-8") as f:
                shell = f.read().strip().lstrip("-") in shells
        except OSError:
            shell = False
    procs = cc_process.table()
    argv = None
    todo, seen = ([root] if root is not None else []), set()
    while todo and argv is None:
        pid = todo.pop()
        if pid in seen:
            continue
        seen.add(pid)
        row = procs.get(pid)
        if row and pid != root and cc_process.claude_argv(row[1]):
            argv = list(row[1])
            break
        todo.extend(p for p, r in procs.items() if r[0] == pid)
    me, daemon = os.getuid(), False
    for pid, row in procs.items():
        if cc_process.claude_argv(row[1]) and "daemon" in row[1][1:4]:
            try:
                if os.stat("/proc/%d" % pid).st_uid != me:
                    continue
            except OSError:
                pass
            daemon = True
            break
    return {"shell": shell, "argv": argv, "daemon": daemon}


def login_seen(home=None):
    """Is there a Claude Code login file here (its size only, never its contents)? Not seeing one does not mean no
    login (a mac keeps it in the keychain, an API key works without it: manager 11-20): only said on the card."""
    try:
        return os.path.getsize(os.path.join(home or os.path.expanduser("~"), ".claude", ".credentials.json")) > 0
    except OSError:
        return False
