"""tmux:// identifiers of panes (C-92), and this machine's panes listed and read by them.

    tmux://<machine>[/<socket>]/<session>:<window>.<pane>#%<pane id>

- <machine>: its name in the machine list (this one too).
- <socket>: none for the user's default tmux server; a name for another server in the user's tmux folder (as
  `tmux -L <name>`); a socket anywhere else is its absolute path, percent-encoded, so it starts with %2F. A path of
  one segment is the default server's; of two, the first is the socket (a "/" in a session's name is always encoded).
- <session>:<window>.<pane>: where the pane is, as tmux writes a target; each part percent-encoded.
- #%<pane id>: tmux's own id of the pane, as it is (not encoded). Always in what list() gives; one written by hand may
  leave it out, and then read() reads by the place alone. Where it is given, the pane now at that place must be that
  one, or nothing is read: the place is never looked up by the id instead (ids are reused once a server restarts).

Standard library only, Python 3.8 is enough: tools/dcremote imports it on the other machines (tmux-list, tmux-read).
"""
import os
import pwd
import re
import subprocess
import urllib.parse

SCHEME = "tmux://"
LINES, LINES_MAX = 50, 2000  # read(): rows of history by default, and at most
TIMEOUT = 10
FORMAT = "\t".join(("#{pane_id}", "#{session_name}", "#{window_index}", "#{pane_index}", "#{pane_current_command}",
                    "#{pane_current_path}", "#{pane_pid}"))
PANE_ID = re.compile(r"%\d+")


class BadId(ValueError):
    """Not a tmux:// identifier this module can read (exit 2 in altention tmux)."""


def socket_dir():
    """The user's tmux folder, where `tmux -L <name>` puts its sockets (as tmux itself works it out)."""
    return os.path.join(os.environ.get("TMUX_TMPDIR") or "/tmp", "tmux-%d" % os.getuid())


def _q(part):
    return urllib.parse.quote(str(part), safe="")


def socket_segment(path, folder=None):
    """What the identifier says of the server at socket `path`: "" for the default one, its name for another in the
    tmux folder, else its absolute path encoded."""
    folder = folder or socket_dir()
    if os.path.dirname(path) == folder:
        name = os.path.basename(path)
        return "" if name == "default" else _q(name)
    return _q(os.path.abspath(path))


def ident(machine, path, session, window, pane, pane_id=None, folder=None):
    seg = socket_segment(path, folder)
    return "%s%s%s/%s:%s.%s%s" % (SCHEME, _q(machine), "/" + seg if seg else "", _q(session), _q(window), _q(pane),
                                  "#" + pane_id if pane_id else "")


def parse(text):
    """{"machine", "socket" (None: the default server; a name; an absolute path), "session", "window", "pane",
    "pane_id" (None when not written)}; BadId when it is not one."""
    if not isinstance(text, str) or not text.startswith(SCHEME):
        raise BadId("不是 tmux:// 開頭的識別碼：%s" % text)
    rest, _, pane_id = text[len(SCHEME):].partition("#")
    if pane_id and not PANE_ID.fullmatch(pane_id):
        raise BadId("# 後面要是窗格編號（像 %%12）：%s" % pane_id)
    parts = rest.split("/")
    if len(parts) == 2:
        machine, sock, place = parts[0], None, parts[1]
    elif len(parts) == 3:
        machine, sock, place = parts
    else:
        raise BadId("識別碼要是 tmux://機器[/socket]/session:window.pane：%s" % text)
    session, colon, wp = place.rpartition(":")
    window, dot, pane = wp.partition(".")
    if not (machine and colon and dot and session and window.isdigit() and pane.isdigit()):
        raise BadId("位置要寫成 session:window.pane（window 和 pane 是數字）：%s" % text)
    sock = urllib.parse.unquote(sock) if sock else None
    if sock is not None and not sock.startswith("/") and "/" in sock:
        raise BadId("socket 名字裡不能有 /：%s" % sock)
    return {"machine": urllib.parse.unquote(machine), "socket": sock, "session": urllib.parse.unquote(session),
            "window": window, "pane": pane, "pane_id": pane_id or None}


def socket_path(sock, folder=None):
    folder = folder or socket_dir()
    if sock is None:
        return os.path.join(folder, "default")
    return sock if sock.startswith("/") else os.path.join(folder, sock)


def tmux(path, *args):
    """(exit code, stdout, stderr) of tmux on the server at socket `path`; 124 when it does not answer in TIMEOUT."""
    env = dict(os.environ, PATH="%s/.local/bin:/opt/homebrew/bin:/usr/local/bin:%s" % (os.path.expanduser("~"),
                                                                                      os.environ.get("PATH", "")))
    try:
        r = subprocess.run(["tmux", "-S", path] + list(args), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           universal_newlines=True, timeout=TIMEOUT, env=env)
    except subprocess.TimeoutExpired:
        return 124, "", "tmux 沒有回應"
    except OSError as e:
        return 127, "", str(e)
    return r.returncode, r.stdout, r.stderr.strip()


def sockets(folder=None):
    """The sockets in the user's tmux folder (a server may or may not be behind each)."""
    import stat as stat_
    folder = folder or socket_dir()
    try:
        names = sorted(os.listdir(folder))
    except OSError:
        return []
    out = []
    for n in names:
        p = os.path.join(folder, n)
        try:
            if stat_.S_ISSOCK(os.stat(p).st_mode):
                out.append(p)
        except OSError:
            pass
    return out


def _stat_fields(pid):
    """The fields of /proc/<pid>/stat after the command's name: [state, ppid, pgrp, session, tty_nr, tpgid, …]."""
    with open("/proc/%d/stat" % int(pid)) as f:
        return f.read().rsplit(")", 1)[1].split()


def groups():
    """{process group: [pid, …]} of every process /proc shows (Linux), read once for many panes (list_panes: one
    pass over /proc for the whole listing, not one a pane: 43 panes took 0.7 s, ACT-71 A); {} without /proc."""
    out = {}
    try:
        names = os.listdir("/proc")
    except OSError:
        return out
    for name in names:
        if name.isdigit():
            try:
                out.setdefault(_stat_fields(name)[2], []).append(name)
            except (OSError, IndexError, ValueError):
                continue
    return out


def foreground(pane_pid, by_group=None):
    """The users of the pane's foreground process group other than this one ([] when they are all this user's), or
    None when it cannot be told (send refuses then; list shows "?"). `by_group`: groups() already read (Linux)."""
    me = os.getuid()
    try:
        if os.path.isdir("/proc/%d" % int(pane_pid)):
            tpgid = _stat_fields(pane_pid)[5]
            uids = set()
            members = (groups() if by_group is None else by_group).get(tpgid, [])
            for name in members:
                try:
                    with open("/proc/%s/status" % name) as f:
                        for row in f:
                            if row.startswith("Uid:"):
                                uids.update(int(u) for u in row.split()[1:3])
                except (OSError, IndexError, ValueError):
                    continue
        else:  # macOS: ps
            r = subprocess.run(["ps", "-o", "tpgid=,tty=", "-p", str(int(pane_pid))], stdout=subprocess.PIPE,
                               stderr=subprocess.DEVNULL, universal_newlines=True, timeout=TIMEOUT)
            tpgid, tty = r.stdout.split()[:2]
            r = subprocess.run(["ps", "-o", "pgid=,uid=,ruid=", "-t", tty], stdout=subprocess.PIPE,
                               stderr=subprocess.DEVNULL, universal_newlines=True, timeout=TIMEOUT)
            uids = set()
            for row in r.stdout.splitlines():
                f = row.split()
                if len(f) == 3 and f[0] == tpgid:
                    uids.update((int(f[1]), int(f[2])))
        if not uids:
            return None
    except (OSError, IndexError, ValueError, subprocess.SubprocessError):
        return None
    others = []
    for uid in sorted(uids - {me}):
        try:
            others.append(pwd.getpwuid(uid).pw_name)
        except KeyError:
            others.append(str(uid))
    return others


def _channel_of(pid, routed):
    """The Discord channel a pane is followed in: the route table's (an attached pane), else DCBOT_CHANNEL in the
    environment of what the pane was started with (one the bot opened). None when neither says."""
    if routed:
        return str(routed)
    try:
        with open("/proc/%d/environ" % int(pid), "rb") as f:
            for item in f.read().split(b"\0"):
                if item.startswith(b"DCBOT_CHANNEL="):
                    value = item.split(b"=", 1)[1].decode("ascii", "replace")
                    return value if value.isdigit() else None
    except (OSError, ValueError):
        pass
    return None


def list_panes(machine, is_claude, routed=lambda key, pid: None, folder=None):
    """Every pane of every tmux server of this user here (the bot's own too): [{"id", "socket", "session", "window",
    "pane", "pane_id", "command", "cwd", "claude", "others": the users of its foreground other than this one ([]: none;
    None: not known), "channel"}]. `routed(key, pid)`: the channel the route table gives "<socket>|<pane id>";
    `is_claude(command, pid)`: whether the pane whose foreground program tmux names `command`, started as `pid`, runs
    Claude Code (dcremote gives altention's process.Lazy(): the command lines under it, not the name)."""
    folder = folder or socket_dir()
    out, by_group = [], []
    for path in sockets(folder):
        code, text, _ = tmux(path, "list-panes", "-a", "-F", FORMAT)
        if code != 0:  # a socket with no server behind it
            continue
        for row in text.splitlines():
            f = row.split("\t")
            if len(f) != 7:
                continue
            pane_id, session, window, pane, command, cwd, pid = f
            if not by_group and os.path.isdir("/proc"):
                by_group.append(groups())
            out.append({"id": ident(machine, path, session, window, pane, pane_id, folder), "socket": path,
                        "session": session, "window": window, "pane": pane, "pane_id": pane_id, "command": command,
                        "cwd": cwd, "claude": bool(is_claude(command, pid)),
                        "others": foreground(pid, by_group[0] if by_group else None),
                        "channel": _channel_of(pid, routed("%s|%s" % (path, pane_id), pid))})
    return out


def at(ref, folder=None):
    """(socket path, the pane id now at the place `ref` names) or (socket path, None) when no pane is there. The place
    the answer is of is printed with it and compared: tmux 3.4's display-message answers for the current pane, without
    an error, when the target names a window or pane that is not there (tried 2026-11-03)."""
    path = socket_path(ref["socket"], folder)
    code, text, _ = tmux(path, "display-message", "-p", "-t", "=%s:%s.%s" % (ref["session"], ref["window"], ref["pane"]),
                         "#{pane_id}\t#{session_name}\t#{window_index}\t#{pane_index}")
    f = text.rstrip("\n").split("\t")
    if code != 0 or len(f) != 4 or not PANE_ID.fullmatch(f[0]) or f[1:] != [ref["session"], ref["window"], ref["pane"]]:
        return path, None
    return path, f[0]


def read(ref, lines=LINES, folder=None):
    """{"text": the pane's last `lines` rows of history (joined where they wrapped), "pane_id"} or {"error", "code":
    3 (no pane there, or another one than #% says: "now" is the one there)}."""
    lines = max(1, min(int(lines), LINES_MAX))
    path, now = at(ref, folder)
    if now is None:
        return {"error": "那個位置沒有窗格（%s:%s.%s）" % (ref["session"], ref["window"], ref["pane"]), "code": 3}
    if ref.get("pane_id") and ref["pane_id"] != now:
        return {"error": "那個位置現在是窗格 %s，不是 %s（視窗被重排或 tmux 重開過）：請重新 list" % (now, ref["pane_id"]),
                "code": 3, "now": now}
    code, text, err = tmux(path, "capture-pane", "-p", "-J", "-S", "-%d" % lines, "-t", now)
    if code != 0:
        return {"error": "讀不到：%s" % (err or "tmux 結束碼 %s" % code), "code": 3}
    # -S -N is N rows of history above the screen and all the screen: the last `lines` that hold something are kept
    rows = text.rstrip("\n").split("\n")
    while rows and not rows[-1].strip():
        rows.pop()
    return {"text": masked("\n".join(rows[-lines:]) + ("\n" if rows else "")), "pane_id": now}


def masked(text):
    """`text` with the tokens and keys it can tell masked, as the bot masks a screen it posts (bot/masking.screen: keys
    wrapped across rows too), here on the machine it was read on: what dcremote tmux-read gives is masked already, a
    plugin that runs it over its own SSH line included (dcremote.tmux; manager 2026-11-03). Masking again changes
    nothing (altention tmux read does, before it prints). Not a promise: a format it does not know shows as it is."""
    from bot import masking  # (standard library and the engine's altention.redact: there on every machine)
    out = masking.screen(text)[0]
    return out if out.endswith("\n") or not text.endswith("\n") else out + "\n"
