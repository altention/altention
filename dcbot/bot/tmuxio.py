"""Drive a terminal program inside tmux: start it, type into it, read its screen.

Knows nothing about Discord or hooks. Two kinds of target, accepted by every function that takes `t`:
- a session name (str): a session the bot started on its own tmux server (the name given to configure()), so it
  survives bot restarts and the owner can attach to it;
- a pane address (dict {"socket": path, "id": "%3"}): a pane on any tmux server of this user, typically a
  Claude Code the owner started themselves. The bot types into it and reads it, but never kills it.
"""
import asyncio
import json
import os
import re
import time

from altention.connectors.claude_code import screen as cc_screen  # (this module has a screen() of its own)
from altention.connectors.claude_code import typist as cc_typing


# How a message is typed (the pace, the new-line key, the Escapes' gap, vim) is the connector's: cc_typing.
WIDTH, HEIGHT = 120, 50
# What people type for a key -> tmux's name. An unknown name is not an error for tmux: it types the letters.
KEY_NAMES = {"esc": "Escape", "escape": "Escape", "enter": "Enter", "return": "Enter", "tab": "Tab",
             "space": "Space", "backspace": "BSpace", "up": "Up", "down": "Down", "left": "Left", "right": "Right"}


# The last Escape sent to each pane (escape() keeps cc_typing.ESC_GAP from it)
_esc_at = {}


def _pane_key(t):
    return json.dumps(t, sort_keys=True) if isinstance(t, dict) else str(t)


_set = {}  # what configure() was told: nothing until it is called


def configure(socket=None, user_tmux="", skip=()):
    """Tell this module, once, which tmux servers it works with: `socket`, the name of the bot's own server
    (`tmux -L <name>`; None for a program that has none, like tools/dcremote on another machine), `user_tmux`,
    the owner's own socket when it is not the default one, and `skip`, names of servers whose panes are not the
    owner's to attach (dcremote: the server of a bot installed on that machine). The bot does it when it starts
    (main.main(), nowhere else: importing its modules tells this module nothing, so a test never holds the name of
    the server of the bot that is running); a tool for itself. Until stage 3b step 1.8 this module read the bot's
    settings module, and with it every program that imported it read the bot's settings file."""
    _set.clear()
    _set.update(socket=socket, user_tmux=user_tmux or "", skip=tuple(skip), done=True)


def bot_socket():
    """The name of the bot's own tmux server. Asked for before configure(), or by a program that said it has none:
    an error, rather than a command sent to whatever server a default would name."""
    if not _set.get("done"):
        raise RuntimeError("tmuxio.configure() has not been called: which tmux server is the bot's is not known")
    if not _set.get("socket"):
        raise RuntimeError("this program has no tmux server of the bot's (tmuxio.configure(socket=None))")
    return _set["socket"]


async def tmux(*args, socket=None, host=None, err=None):
    """Run one tmux command, on the bot's server or on the server at `socket` (on machine `host`, over SSH,
    if given). Returns (returncode, stdout); `err`, a list: why it failed is appended to it."""
    if host:
        from . import remote
        return await remote.tmux(host, socket, args, err=err)
    where = ["-S", socket] if socket else ["-L", bot_socket()]
    proc = await asyncio.create_subprocess_exec(
        "tmux", "-u", *where, *args,  # -u: UTF-8 even when started from cron without LANG
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    out, stderr = await proc.communicate()
    if proc.returncode != 0 and err is not None and stderr.strip():
        from .remote import said
        err.append(said(stderr.decode("utf-8", "replace")))
    return proc.returncode, out.decode("utf-8", "replace")


def at(t):
    """(socket, tmux target) for a session name or a pane address."""
    if isinstance(t, dict):
        return t["socket"], t["id"]
    return None, "=%s:" % t


def on(t):
    """The keyword arguments that send a tmux command to wherever `t` lives."""
    return {"socket": t["socket"], "host": t.get("host")} if isinstance(t, dict) else {}


def label(t):
    """How to tell the owner where a target is."""
    if isinstance(t, dict):
        return "%stmux -S %s（窗格 %s）" % ("%s 上的 " % t["host"] if t.get("host") else "", t["socket"], t["id"])
    return "tmux -L %s attach -t %s" % (bot_socket(), t)


class Pane:
    """One place where an agent runs, as the code that opens a conversation sees it: its screen can be read, keys
    pressed and text typed into it, and it can be asked whether it is still there. Made here from what a channel
    keeps (`t`: the name of a session on the bot's own server, or the address of a pane, on this machine or
    another); tests make the same thing over a fake (tests/fakepane.py). Nothing is decided here: each method is
    the function of this module that bears its name, looked up when called.
    (3b step 2.4a: only the opening of a conversation goes through it so far; typing, menus and /btw in 2.7a.)"""

    def __init__(self, t):
        self.t = t

    @property
    def remote(self):
        """Is it on another machine?"""
        return bool(isinstance(self.t, dict) and self.t.get("host"))

    @property
    def label(self):
        return label(self.t)

    async def alive(self):
        return await alive(self.t)

    async def standing(self):
        return await standing(self.t)

    async def raw_screen(self):
        return await raw_screen(self.t)

    async def screen(self, lines=0):
        return await screen(self.t, lines)

    async def keys(self, *names):
        return await keys(self.t, *names)

    async def type_text(self, text):
        return await type_text(self.t, text)

    async def cursor(self):
        return await cursor(self.t)

    async def color_screen(self):
        return await color_screen(self.t)

    async def in_mode(self):
        return await in_mode(self.t)

    async def vim_mode(self):
        return await vim_mode(self.t)

    async def escape(self, needed):
        return await escape(self.t, needed)

    async def close(self):
        """End what the bot itself started there: its own session, or the window it opened on the owner's server."""
        if isinstance(self.t, dict):
            await kill_pane(self.t)
        else:
            await kill(self.t)


def pane(t):
    """The Pane of a target; a pane itself (this module's, or the tests' own over a fake) as it is; None for nothing."""
    return t if t is None or hasattr(t, "raw_screen") else Pane(t)


def session_name(channel_name, channel_id):
    """A tmux session name for a channel: readable, and unique even if two channels share a name."""
    safe = re.sub(r"[^\w-]", "_", channel_name or "dm")[:30]
    return "dc-%s-%s" % (safe, str(channel_id)[-4:])


def plan_keys(text):
    """A message as send-keys steps: ("text", s) is typed literally, ("key", k) is a key press. What to send is the
    connector's (cc_typing.steps(): stretches of text, new lines); here only what tmux itself needs: a trailing ";"
    is a command separator to send-keys, so "\\;" is sent for it."""
    out = []
    for step in cc_typing.steps(text):
        if step[0] == "newline":
            out.append(("key", cc_typing.NEWLINE))
        elif step[1].endswith(";"):
            if step[1][:-1]:
                out.append(("text", step[1][:-1]))
            out.append(("text", "\\;"))
        else:
            out.append(step)
    return out


def key_name(key):
    return KEY_NAMES.get(key.lower(), key)


async def alive(t):
    """Is the session there? For a pane address: is it still running Claude (not back at a shell)?"""
    if isinstance(t, dict) and t.get("shell"):  # a terminal channel's shell: there is enough
        return await exists(t)
    if isinstance(t, dict):
        code, out = await tmux("display-message", "-p", "-t", t["id"], "#{pane_current_command}\t#{pane_pid}", **on(t))
        asked(t, answered=not (t.get("host") and code == 255))  # ssh's own failure: the question never got an answer
        command, _, pid = out.strip().partition("\t")
        if t.get("pid") and pid != str(t["pid"]):
            return False  # pane numbers start over when a tmux server restarts: that is someone else's pane now
        return code == 0 and cc_screen.is_claude(command)
    return (await tmux("has-session", "-t", "=" + t))[0] == 0


UNANSWERED = set()  # the panes on other machines whose last asking (alive()) got no answer at all


def asked(t, answered):
    """Note whether the last asking of a pane got an answer (alive(), and whoever stands in for it in a test)."""
    key = (t.get("host"), t.get("socket"), t.get("id"))
    (UNANSWERED.discard if answered else UNANSWERED.add)(key)


def unanswered(t):
    """Did the last asking of this pane (alive()) get no answer at all? Read from memory: nothing is asked."""
    return isinstance(t, dict) and (t.get("host"), t.get("socket"), t.get("id")) in UNANSWERED


async def standing(t):
    """alive(), with the two ways of "no" told apart: "alive"; "gone" (the pane's machine answered, and the pane
    does not run Claude: back at a shell, closed, or its number is someone else's now); "unknown" (the pane is on
    another machine and the asking itself failed: ssh's 255, the machine off, the line dropped). Not known is not
    gone (#issues 29, manager 2026-10-14): nothing is detached, closed, opened anew or called gone on "unknown".
    A session of the bot's own server, or a pane on this machine, is never "unknown"."""
    if await alive(t):
        return "alive"
    if unanswered(t):
        return "unknown"
    return "gone"


async def start(name, cwd, argv, env=None):
    """Start `argv` in a new detached session. The session ends when the program exits."""
    cmd = ["new-session", "-d", "-s", name, "-x", str(WIDTH), "-y", str(HEIGHT), "-c", cwd]
    for k, v in (env or {}).items():
        cmd += ["-e", "%s=%s" % (k, v)]
    ok = (await tmux(*(cmd + list(argv))))[0] == 0
    if ok:  # what Claude Code copies ("c" in the /btw answer) lands in a tmux buffer the bot can read
        await tmux("set-option", "-s", "set-clipboard", "on")
    return ok


async def clipboard(t):
    """The `set-clipboard` option of the server of `t`, as tmux shows it ("" when it cannot be read)."""
    return (await tmux("show-options", "-s", "-v", "set-clipboard", **on(t)))[1].strip()


async def buffers(t):
    out = (await tmux("list-buffers", "-F", "#{buffer_name}", **on(t)))[1]
    return [b for b in out.split("\n") if b]


async def take_buffer(t, name):
    """Read a tmux buffer and delete it."""
    text = (await tmux("show-buffer", "-b", name, **on(t)))[1]
    await tmux("delete-buffer", "-b", name, **on(t))
    return text


async def keys(t, *names):
    """Press named keys (Enter, Escape, Down, M-Enter, …). Any Escape among them is noted for escape()."""
    socket, target = at(t)
    await tmux("send-keys", "-t", target, *[key_name(k) for k in names], **on(t))
    if any(key_name(k) == "Escape" for k in names):
        _esc_at[_pane_key(t)] = time.monotonic()


async def escape(t, needed):
    """An Escape the bot decides on (the user's own `!key Escape` does not come here): sent only while
    `needed(raw screen)` holds, and never within ESC_GAP of the last Escape to this pane (it waits, then looks
    again), so two of them can never open Rewind. True if sent; False if not, because nothing needed it."""
    if not needed(await raw_screen(t)):
        return False
    wait = cc_typing.ESC_GAP - (time.monotonic() - _esc_at.get(_pane_key(t), float("-inf")))
    if wait > 0:
        await asyncio.sleep(wait)
        if not needed(await raw_screen(t)):
            return False
    await keys(t, "Escape")
    return True


async def type_text(t, text):
    """Type `text` into the pane as typed, never pasted, at the connector's pace (cc_typing.paced(): a pause of
    TYPE_PAUSE wherever what arrives in one go would pass TYPE_CHUNK)."""
    socket, target = at(t)
    for step in cc_typing.paced(plan_keys(text)):
        if step[0] == "pause":
            await asyncio.sleep(cc_typing.TYPE_PAUSE)
        elif step[0] == "text":
            await tmux("send-keys", "-t", target, "-l", "--", step[1], **on(t))
        else:
            await keys(t, step[1])


async def raw_screen(t):
    """The screen exactly as shown, blank lines and rules included."""
    socket, target = at(t)
    return (await tmux("capture-pane", "-p", "-t", target, **on(t)))[1]


async def color_screen(t):
    """(the screen with its colours as SGR escape sequences, the pane's width in columns)."""
    socket, target = at(t)
    text = (await tmux("capture-pane", "-p", "-e", "-t", target, **on(t)))[1]
    cols = (await tmux("display-message", "-p", "-t", target, "#{pane_width}", **on(t)))[1].strip()
    return text, int(cols) if cols.isdigit() else None


async def cursor(t):
    """(column, row) of the cursor on the pane's screen, both from 0; None when tmux does not say."""
    socket, target = at(t)
    out = (await tmux("display-message", "-p", "-t", target, "#{cursor_x} #{cursor_y}", **on(t)))[1].split()
    return (int(out[0]), int(out[1])) if len(out) == 2 and all(v.isdigit() for v in out) else None


async def in_mode(t):
    """Is the pane in a tmux mode (copy mode: the owner scrolling back)? Keys sent then go to tmux, not to
    what runs in the pane."""
    socket, target = at(t)
    return (await tmux("display-message", "-p", "-t", target, "#{pane_in_mode}", **on(t)))[1].strip() not in ("", "0")


async def vim_mode(t):
    """May the Claude Code in this pane have vim as its editor mode? From the settings on the pane's machine:
    the user's, the managed ones and those of the folder the pane is in. Not known is yes. (A --settings file
    given on its command line is not seen.)"""
    socket, target = at(t)
    code, cwd = await tmux("display-message", "-p", "-t", target, "#{pane_current_path}", **on(t))
    cwd = cwd.strip()
    if code != 0 or not cwd:
        return True
    from .remote import quote, run
    command = cc_typing.EDITOR_MODE % " ".join(quote(os.path.join(cwd, ".claude", n)) for n in cc_typing.FOLDER_SETTINGS)
    host = t.get("host") if isinstance(t, dict) else None
    if host:
        code, out = await run(host, command)
    else:
        proc = await asyncio.create_subprocess_exec("sh", "-c", command, stdout=asyncio.subprocess.PIPE,
                                                    stderr=asyncio.subprocess.DEVNULL)
        out = (await proc.communicate())[0].decode("utf-8", "replace")
        code = proc.returncode
    return code != 0 or cc_typing.says_vim(out)


async def screen(t, lines=0):
    """What the session shows right now, compacted; the last `lines` lines if given."""
    out = await raw_screen(t)
    rows = [r.rstrip() for r in out.split("\n") if r.strip(" ─")]  # no blank lines, no horizontal rules
    return "\n".join(rows[-lines:] if lines else rows)


async def kill(t):
    """End a session the bot started. A pane address is the owner's own terminal: left alone."""
    if not isinstance(t, dict):
        await tmux("kill-session", "-t", "=" + t)


def user_socket():
    """The owner's own tmux server (the one plain `tmux` talks to)."""
    if not _set.get("done"):
        raise RuntimeError("tmuxio.configure() has not been called: which tmux server is the owner's is not known")
    return _set["user_tmux"] or os.path.join(socket_dir(), "default")


async def user_sessions(socket=None, host=None):
    """[(session name, folder of its first window)] on the owner's tmux server (on machine `host` if given)."""
    code, out = await tmux("list-sessions", "-F", "#{session_name}\t#{pane_current_path}", socket=socket or user_socket(),
                           host=host)
    if code != 0:
        return []
    return [tuple(row.split("\t", 1)) for row in out.split("\n") if "\t" in row]


async def open_window(socket, session, window_name, cwd, argv, env=None, host=None, why=None):
    """Start `argv` in a new window of `session` on the server at `socket` (the session is created if it
    does not exist), on machine `host` if given. Returns the new pane's address, or None; then `why` (a list, if
    given) gets what tmux, the shell or ssh said, also printed for supervisor.out."""
    said = []
    code = (await tmux("has-session", "-t", "=" + session, socket=socket, host=host, err=said))[0]
    if host and code == 255:  # did not get there: whether the session exists is not known, nothing more is sent
        return _not_opened(host, session, said, why)
    exists = code == 0
    if not exists and socket and any("error connecting" in s and "No such file or directory" in s for s in said):
        await socket_folder(socket, host)  # no server, not even its folder: a machine just restarted
    cmd = ["new-window", "-d", "-t", "=%s:" % session] if exists else \
        ["new-session", "-d", "-s", session, "-x", str(WIDTH), "-y", str(HEIGHT)]
    cmd += ["-P", "-F", "#{pane_id}\t#{pane_pid}", "-c", cwd] + (["-n", window_name] if window_name else [])
    if host and env:  # tmux before 3.2 (Ubuntu 20.04 has 3.0a) has no new-session -e: let env(1) set them; by its
        # full path: a bare `env` may find something else first in PATH (uv's installer leaves ~/.local/bin/env, a
        # script for `source` that is executable and does nothing: the window ended at once; 2026-10-07)
        argv = ["/usr/bin/env"] + ["%s=%s" % kv for kv in env.items()] + list(argv)
    else:
        for k, v in (env or {}).items():
            cmd += ["-e", "%s=%s" % (k, v)]
    tried = []
    code, out = await tmux(*(cmd + list(argv)), socket=socket, host=host, err=tried)
    pane, _, pid = out.strip().partition("\t")
    if code != 0 or not pane.startswith("%"):
        # has-session's own failure only matters when the next step failed too ("can't find session" is how it
        # says no; anything else, like the shell's "not found", explains a "duplicate session" that follows)
        return _not_opened(host, session, said + (tried or ["%s 結束碼 %s，沒有給新視窗的編號" % (cmd[0], code)]), why)
    return dict({"socket": socket, "id": pane, "pid": pid}, **({"host": host} if host else {}))


async def socket_folder(socket, host=None):
    """Make the folder a server's socket lies in (owner only), on machine `host` if given. tmux makes the socket but
    not its folder: after a restart /tmp/tmux-<uid> is gone until a server is started there by default, and a
    new-session at a socket in it said "error creating …" and opened nothing (a pane of another machine's taken back
    out of the archive, its window gone with the restart: manager 2026-10-14). Whatever goes wrong, new-session says."""
    folder = os.path.dirname(socket)
    if not folder:
        return
    if host:
        from . import remote
        await remote.run(host, "mkdir -p -m 700 %s" % remote.quote(folder))
        return
    try:
        os.makedirs(folder, mode=0o700, exist_ok=True)
    except OSError:
        pass


def _not_opened(host, session, said, why):
    text = "；".join(dict.fromkeys(s for s in said if s))
    print("open_window: %s%s: %s" % ("%s 上的 " % host if host else "", session, text or "沒有說原因"), flush=True)
    if why is not None and text:
        why.append(text)
    return None


async def pane_pid(pane):
    """The process a pane was started with (its shell, or the program it was opened for)."""
    code, out = await tmux("display-message", "-p", "-t", pane["id"], "#{pane_pid}", **on(pane))
    return out.strip() if code == 0 else None


async def exists(pane):
    """Is the pane still there, with the same process? (A shell: whatever runs in it.)"""
    code, out = await tmux("display-message", "-p", "-t", pane["id"], "#{pane_pid}", **on(pane))
    return code == 0 and (not pane.get("pid") or out.strip() == str(pane["pid"]))


async def kill_pane(pane):
    """Close a window the bot opened itself on the owner's server (never anything else)."""
    await tmux("kill-pane", "-t", pane["id"], **on(pane))


def safe_session(name):
    """A tmux session name: tmux reads "." and ":" as target separators."""
    return re.sub(r"[.:\s]+", "-", name.strip())[:50]


def socket_dir():
    return os.path.join(os.environ.get("TMUX_TMPDIR") or "/tmp", "tmux-%d" % os.getuid())


# automatic-rename is 0 once the owner has given the window a name of their own
PANE_FORMAT = ("#{pane_id}\t#{session_name}\t#{window_index}\t#{window_name}\t#{pane_current_command}"
               "\t#{pane_current_path}\t#{automatic-rename}\t#{pane_pid}")


def parse_panes(socket, out):
    """`tmux list-panes -a -F PANE_FORMAT` output -> the panes running Claude Code."""
    found = []
    for row in out.split("\n"):
        f = row.split("\t")
        if len(f) == 8 and cc_screen.is_claude(f[4]):
            found.append({"socket": socket, "id": f[0], "session": f[1], "window": f[2], "cwd": f[5],
                          "window_name": f[3] if f[6] == "0" else "", "pid": f[7]})
    return found


async def claude_panes(everywhere=False):
    """Every pane running Claude Code on this user's tmux servers, except the bot's own server. `everywhere`:
    also on the other machines sessions can be attached from (each pane then says which: "host")."""
    found = await local_claude_panes()
    if everywhere:
        from . import remote
        for host in remote.hosts():
            code, out = await remote.dcremote(host, "panes", timeout=20)
            try:
                found += [dict(p, host=host) for p in json.loads(out)] if code == 0 else []
            except ValueError:
                pass
    return found


async def local_claude_panes():
    d, found = socket_dir(), []
    try:
        names = sorted(os.listdir(d))
    except OSError:
        return []
    if not _set.get("done"):
        raise RuntimeError("tmuxio.configure() has not been called: which tmux server is the bot's is not known")
    for n in names:
        if n == _set.get("socket") or n in _set.get("skip", ()) or n.startswith(("dcbot-", "dctest")):
            continue  # the bot's sessions already have channels; test servers are not the owner's work
        socket = os.path.join(d, n)
        code, out = await tmux("list-panes", "-a", "-F", PANE_FORMAT, socket=socket)
        if code == 0:  # a socket file with no server behind it just fails
            found += parse_panes(socket, out)
    return found


async def own_sessions():
    """Every session on the bot's own tmux server, as tmux tells of it: [{"name", "at": when anything was last
    drawn in it, "attached": how many clients are attached, "panes": [(the pid of what the pane was started
    with, the command it runs now)]}]. Nothing is judged here, least of all by the name.

    "at" is tmux's window_activity, the newest of the session's windows: it follows what the program draws, and
    stands still while Claude Code sits idle, a background command of its running or not (watched 2026-10-11).
    Not session_activity: with no client attached that one stays at the time the session was made."""
    out = (await tmux("list-panes", "-a", "-F", "#{session_name}\t#{window_activity}\t#{session_attached}\t#{pane_pid}\t#{pane_current_command}"))[1]
    found = {}
    for row in out.split("\n"):
        parts = row.split("\t")
        if len(parts) != 5 or not parts[0] or not all(p.isdigit() for p in parts[1:4]):
            continue
        name, at, attached, pid, command = parts
        one = found.setdefault(name, {"name": name, "at": 0, "attached": int(attached), "panes": []})
        one["at"] = max(one["at"], int(at))
        one["panes"].append((int(pid), command))
    return [found[name] for name in sorted(found)]


def started_for(pid):
    """(DCBOT_ROOT, DCBOT_CHANNEL) in the environment a process was started with, as /proc tells: what the bot
    gives the Claude Code it starts for a channel. None when that cannot be read (not Linux, not this user's,
    gone) or either is not there."""
    try:
        with open("/proc/%d/environ" % pid, "rb") as f:
            env = dict(e.split(b"=", 1) for e in f.read().split(b"\0") if b"=" in e)
    except (OSError, ValueError):
        return None
    root, channel = env.get(b"DCBOT_ROOT"), env.get(b"DCBOT_CHANNEL")
    if not root or not channel:
        return None
    return root.decode("utf-8", "replace"), channel.decode("utf-8", "replace")


def children(pid):
    """The pids of the processes started by this one, as /proc tells; None when that cannot be told (not Linux,
    /proc not readable). A Claude Code sitting idle has none; a background command of its is a shell under it."""
    try:
        names = [n for n in os.listdir("/proc") if n.isdigit()]
    except OSError:
        return None
    out = []
    for n in names:
        try:
            with open("/proc/%s/stat" % n) as f:
                stat = f.read()
            if int(stat.rsplit(")", 1)[1].split()[1]) == pid:
                out.append(int(n))
        except (OSError, ValueError, IndexError):
            continue  # gone meanwhile
    return out


async def sessions():
    out = (await tmux("list-sessions", "-F", "#{session_name}"))[1]
    return [s for s in out.split("\n") if s]
