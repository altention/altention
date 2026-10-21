"""Reading folders and files for the file browser (bot/files.py), here or on another machine (dcremote files).

Standard library only, Python 3.8 is enough: tools/dcremote imports it on the other machines. One entry point,
`op(request) -> result`, both JSON:
  {"op": "list", "path", "hidden"}            a folder: entries (folders first), git branch and marks
  {"op": "read", "path", "start", "count"}    lines [start, start+count) of a text file, or what kind of file it is
  {"op": "raw", "path"}                       the whole file, base64 (images, attachments; up to RAW_MAX bytes)
  {"op": "find", "root", "query", "hidden"}   files and folders under root whose path matches the query, best first
  {"op": "resolve", "cwd", "paths"}           which of these paths (relative to cwd) exist: [[given, absolute, is folder]]
Every result has "error" when it failed. Secrets are never shown, not even their names (blocked()): nor anything
under /proc, /sys or /dev (the environment and command line of every process of this user are read there, tokens
among them; found by a review on 2026-10-21). Files are read only when they are regular files (open_regular()): no
FIFO, device or socket, which could hang the read or never end.
"""
import base64
import fnmatch
import io
import os
import stat
import subprocess

RAW_MAX = 8 * 1024 * 1024
FIND_LIMIT = 25
WALK_LIMIT = 50000
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", ".mypy_cache", ".pytest_cache", ".cache", ".tox"}
IMAGES = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
# Folders under home that hold tokens and keys, and file names that usually do
BLOCKED_DIRS = [".config", ".ssh", ".gnupg", ".aws", ".kube", ".docker", ".password-store", ".local/share/keyrings",
                ".claude/.credentials.json", ".claude.json", ".copilot", ".netrc", ".git-credentials", ".pypirc", ".npmrc"]
SYSTEM_TREES = ["/proc", "/sys", "/dev"]  # whole trees, the folders themselves too (after realpath)
BLOCKED_NAMES = [".env", ".env.*", "*.pem", "*.key", "*.p12", "*.pfx", "id_rsa*", "id_ed25519*", "id_ecdsa*", "id_dsa*",
                 "*credentials*", "*.keystore", ".netrc", ".git-credentials", "secrets.*", "*token*.json", "*.kdbx",
                 ".*history", ".*_history", ".npmrc", ".pypirc", ".viminfo"]


def home():
    return os.path.expanduser("~")


def full(path, cwd=None):
    """An absolute, resolved path ("~" and relative paths allowed)."""
    path = os.path.expanduser(path or "~")
    if not os.path.isabs(path):
        path = os.path.join(os.path.expanduser(cwd or "~"), path)
    return os.path.realpath(path)


def blocked(path):
    """Is this a secret (or inside a folder of secrets)? Checked on the resolved path, so links do not get round it."""
    real = os.path.realpath(path)
    for top in SYSTEM_TREES:
        if real == top or real.startswith(top + os.sep):
            return True
    h = os.path.realpath(home())
    for d in BLOCKED_DIRS:
        top = os.path.join(h, d)
        if real == top or real.startswith(top + os.sep):
            return True
    for part in real.split(os.sep):
        if part and any(fnmatch.fnmatch(part.lower(), pat) for pat in BLOCKED_NAMES):
            return True
    return False


class NotRegular(OSError):
    """What open_regular() refuses: not a regular file, or found to be a blocked one once open."""


def open_regular(path):
    """`path` (resolved and checked by blocked() already) opened for reading in binary, or NotRegular / OSError.
    O_NONBLOCK: a FIFO put there since is opened without waiting for a writer, and then refused by its type;
    O_NOFOLLOW: nor a link put in place of the file. On Linux the open file's own path (/proc/self/fd) is checked by
    blocked() again: a folder on the way swapped for a link since the check gets nowhere."""
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_NOFOLLOW", 0))
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise NotRegular("不是一般的檔案")
        try:
            there = os.readlink("/proc/self/fd/%d" % fd)
        except OSError:
            there = None  # no /proc (macOS): the check before the open stands
        if there is not None and blocked(there):
            raise NotRegular("這個檔案不能看")
        return os.fdopen(fd, "rb")
    except BaseException:
        os.close(fd)
        raise


def git_info(path):
    """{"branch", "dirty", "marks": {entry name: "M" / "?" / "A" / "D"}} for a folder in a git repository, else {}."""
    try:
        r = subprocess.run(["git", "-C", path, "status", "--porcelain=v1", "-b", "-z", "--", "."],
                           stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=5)
        top = subprocess.run(["git", "-C", path, "rev-parse", "--show-toplevel"], stdout=subprocess.PIPE,
                             stderr=subprocess.DEVNULL, universal_newlines=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return {}
    if r.returncode != 0 or top.returncode != 0:
        return {}
    root = os.path.realpath(top.stdout.strip())
    here = os.path.relpath(os.path.realpath(path), root)
    here = "" if here == "." else here + "/"
    out = {"branch": "", "dirty": 0, "marks": {}}
    items = r.stdout.decode("utf-8", "replace").split("\0")
    skip = False
    for item in items:
        if skip:  # the old name of a rename
            skip = False
            continue
        if item.startswith("## "):
            out["branch"] = item[3:].split("...")[0]
            continue
        if len(item) < 4:
            continue
        code, name = item[:2], item[3:]
        if code[0] == "R":
            skip = True
        out["dirty"] += 1
        if not name.startswith(here):
            continue
        first = name[len(here):].split("/")[0]
        mark = "?" if code == "??" else ("A" if "A" in code else "D" if "D" in code else "M")
        out["marks"].setdefault(first, mark)
    return out


def list_dir(path, hidden=False):
    path = full(path)
    if blocked(path):
        return {"error": "這個位置不能看（放金鑰或 token 的地方）"}
    if not os.path.isdir(path):
        return {"error": "找不到資料夾：%s" % path}
    try:
        names = os.listdir(path)
    except OSError as e:
        return {"error": "讀不了：%s" % e.strerror}
    entries = []
    for n in names:
        if n.startswith(".") and not hidden:
            continue
        p = os.path.join(path, n)
        if blocked(p):
            continue
        try:
            is_dir = os.path.isdir(p)
            size = 0 if is_dir else os.path.getsize(p)
        except OSError:
            continue
        e = {"n": n, "d": is_dir, "s": size}
        if is_dir:
            try:
                e["c"] = sum(1 for x in os.listdir(p) if hidden or not x.startswith("."))
            except OSError:
                e["c"] = None
        entries.append(e)
    entries.sort(key=lambda e: (not e["d"], e["n"].lower()))
    return {"path": path, "home": home(), "entries": entries, "git": git_info(path),
            "hidden": sum(1 for n in names if n.startswith("."))}


def kind_of(path):
    if os.path.splitext(path)[1].lower() in IMAGES:
        return "image"
    try:
        with open_regular(path) as f:
            head = f.read(8192)
    except OSError:
        return "unreadable"
    if b"\0" in head:
        return "binary"
    return "text"


def read(path, start=1, count=60):
    path = full(path)
    if blocked(path):
        return {"error": "這個檔案不能看（金鑰或 token）"}
    if os.path.isdir(path):
        return {"error": "這是資料夾", "dir": True, "path": path}
    if not os.path.isfile(path):
        return {"error": "找不到檔案：%s" % path if not os.path.exists(path) else "這不是一般的檔案，不能看"}
    size = os.path.getsize(path)
    kind = kind_of(path)
    out = {"path": path, "home": home(), "size": size, "kind": kind}
    if kind != "text":
        return out
    start, lines, total = max(1, int(start or 1)), [], 0
    try:
        with io.TextIOWrapper(open_regular(path), encoding="utf-8", errors="replace") as f:
            for total, line in enumerate(f, 1):
                if start <= total < start + count:
                    lines.append(line.rstrip("\n").rstrip("\r"))
                elif total >= start + count and size > 50 * 1024 * 1024:
                    total = None  # too big to count all of it
                    break
    except OSError as e:
        return {"error": "讀不了：%s" % (e.strerror or e)}
    if total is not None and start > max(total, 1):  # past the end: show the last lines instead
        return read(path, max(1, total - count + 1), count)
    out.update({"start": start, "lines": lines, "total": total})
    return out


def raw(path):
    path = full(path)
    if blocked(path):
        return {"error": "這個檔案不能看（金鑰或 token）"}
    if not os.path.isfile(path):
        return {"error": "找不到檔案：%s" % path if not os.path.exists(path) else "這不是一般的檔案，不能看"}
    size = os.path.getsize(path)
    if size > RAW_MAX:
        return {"error": "檔案太大（%s），Discord 傳不了" % human(size), "size": size}
    try:
        with open_regular(path) as f:
            data = f.read(RAW_MAX + 1)
    except OSError as e:
        return {"error": "讀不了：%s" % (e.strerror or e)}
    if len(data) > RAW_MAX:  # grown since it was measured
        return {"error": "檔案太大，Discord 傳不了", "size": len(data)}
    return {"path": path, "name": os.path.basename(path), "size": len(data), "b64": base64.b64encode(data).decode()}


def candidates(root, hidden=False):
    """Paths under root, relative: the git repository's files when root is in one (fast, skips what is
    ignored), otherwise a walk that leaves out the usual heavy folders. Folders are listed with a trailing "/"."""
    files = None
    try:
        r = subprocess.run(["git", "-C", root, "ls-files", "-co", "--exclude-standard", "-z"], stdout=subprocess.PIPE,
                           stderr=subprocess.DEVNULL, timeout=10)
        if r.returncode == 0:
            files = [f for f in r.stdout.decode("utf-8", "replace").split("\0") if f][:WALK_LIMIT]
    except (OSError, subprocess.SubprocessError):
        pass
    if files is None:
        files = []
        for dirpath, dirs, names in os.walk(root):
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS and (hidden or not d.startswith("."))]
            rel = os.path.relpath(dirpath, root)
            for n in names:
                if hidden or not n.startswith("."):
                    files.append(n if rel == "." else os.path.join(rel, n))
            if len(files) > WALK_LIMIT:
                break
    folders = set()
    for f in files:
        parts = f.split("/")[:-1]
        for i in range(1, len(parts) + 1):
            folders.add("/".join(parts[:i]) + "/")
    return files + sorted(folders)


def subsequence(needle, hay):
    it = iter(hay)
    return all(c in it for c in needle)


def score(path, tokens):
    """Lower is better; None if some token does not match."""
    low = path.lower()
    base = os.path.basename(low.rstrip("/"))
    total = 0.0
    for t in tokens:
        if t in base:
            total += 0 if base.startswith(t) else 0.5
        elif t in low:
            total += 2
        elif subsequence(t, base):
            total += 3
        elif subsequence(t, low):
            total += 5
        else:
            return None
    return total + len(path) / 200.0


def find(root, query, hidden=False):
    root = full(root)
    if blocked(root) or not os.path.isdir(root):
        return {"error": "找不到資料夾：%s" % root}
    tokens = [t for t in (query or "").lower().split() if t]
    if not tokens:
        return {"root": root, "matches": []}
    scored = []
    for p in candidates(root, hidden):
        s = score(p, tokens)
        if s is not None and not blocked(os.path.join(root, p)):
            scored.append((s, p))
    scored.sort()
    return {"root": root, "home": home(), "matches": [p for _, p in scored[:FIND_LIMIT]], "query": query}


def resolve(cwd, paths):
    found = []
    for given in paths or []:
        p = full(given, cwd)
        if os.path.exists(p) and not blocked(p):
            found.append([given, p, os.path.isdir(p)])
    return {"found": found}


def human(n):
    for unit in ("B", "K", "M", "G"):
        if n < 1024 or unit == "G":
            return ("%d%s" % (n, unit)) if unit == "B" or n >= 100 else ("%.1f%s" % (n, unit))
        n /= 1024.0
    return str(n)


def op(req):
    try:
        what = req.get("op")
        if what == "list":
            return list_dir(req.get("path"), req.get("hidden", False))
        if what == "read":
            return read(req.get("path"), req.get("start", 1), req.get("count", 60))
        if what == "raw":
            return raw(req.get("path"))
        if what == "find":
            return find(req.get("root"), req.get("query"), req.get("hidden", False))
        if what == "resolve":
            return resolve(req.get("cwd"), req.get("paths"))
        return {"error": "不認得的操作：%s" % what}
    except Exception as e:  # a browser click should answer, not crash
        return {"error": "出錯了：%s" % e}
