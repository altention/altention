"""Is Claude Code running in a tmux pane? Told from the command lines of the pane's process and everything under it
that is in the foreground of its terminal, not from the name tmux gives the pane's foreground program: that name is
"node" for a Claude installed with npm and for any other Node program (a dev server), and "bash" for a Claude started
by a script (C-92 b). A Claude stopped with Ctrl-Z or sent to the background (`claude &`) is under the pane too, but
the shell has the terminal and typing there reaches the shell, so it does not count (#code-review-2 on 6e93ae2)."""

import os
import subprocess

from .screen import CLAUDE_VERSION, is_claude

INTERPRETERS = ("node", "nodejs", "bun")  # what runs an npm-installed Claude: `node …/claude-code/cli.js`
SCRIPTS = ("claude-code/cli.js", "claude-code/cli.mjs")
TIMEOUT = 10


def claude_argv(argv):
    """Is this command line (a list of words) Claude Code itself? The native binary (`claude`, or on macOS the
    versioned file it links to, under …/claude/versions/), or a Node-like program running Claude's script or the
    `claude` link npm installs. A word that merely contains "claude" (a path under ~/.claude) is not."""
    if not argv:
        return False
    name = os.path.basename(argv[0])
    if name == "claude" or (CLAUDE_VERSION.match(name) and "/claude/versions/" in argv[0]):
        return True
    if name not in INTERPRETERS:
        return False
    script = next((a for a in argv[1:] if not a.startswith("-")), "")
    return os.path.basename(script) == "claude" or script.endswith(SCRIPTS)


def table():
    """{pid: (parent pid, [words of its command line], its process group, the foreground process group of its
    terminal)} of every process that can be seen here; {} when none can be read. /proc on Linux (stat's fields after
    the name: state, ppid, pgrp, session, tty, tpgid); elsewhere (macOS) `ps`, whose command lines are split at
    spaces."""
    out = {}
    if os.path.isdir("/proc/self"):
        for name in os.listdir("/proc"):
            if not name.isdigit():
                continue
            try:
                with open("/proc/%s/stat" % name, "rb") as f:
                    stat = f.read().rsplit(b")", 1)[1].split()
                with open("/proc/%s/cmdline" % name, "rb") as f:
                    words = [w.decode("utf-8", "replace") for w in f.read().split(b"\0") if w]
                out[int(name)] = (int(stat[1]), words, int(stat[2]), int(stat[5]))
            except (OSError, IndexError, ValueError):
                continue  # gone meanwhile, or not ours to read
        return out
    try:
        r = subprocess.run(["ps", "-axo", "pid=,ppid=,pgid=,tpgid=,args="], stdout=subprocess.PIPE,
                           stderr=subprocess.DEVNULL, universal_newlines=True, timeout=TIMEOUT)
    except (OSError, subprocess.SubprocessError):
        return {}
    for row in r.stdout.splitlines():
        f = row.split()
        if len(f) >= 5 and all(x.lstrip("-").isdigit() for x in f[:4]):
            out[int(f[0])] = (int(f[1]), f[4:], int(f[2]), int(f[3]))
    return out


def under(pid, procs):
    """`pid` and every process under it, from `procs` (table())."""
    children = {}
    for p, row in procs.items():
        children.setdefault(row[0], []).append(p)
    found, todo = [], [pid]
    while todo:
        p = todo.pop()
        if p in found:
            continue
        found.append(p)
        todo += children.get(p, [])
    return found


def runs_claude(command, pid, procs=None):
    """Does the pane whose foreground program tmux names `command`, started as process `pid`, run Claude Code?
    `claude` (or the versioned name on macOS) is, at once; otherwise the command lines of `pid` and everything under
    it in the foreground process group of the pane's terminal (a pgrp equal to the pane process's tpgid) are looked
    at (`procs`: a table() already read, to read it once for many panes). None when that cannot be told: no table,
    or the pane's process not in it."""
    if command == "claude" or CLAUDE_VERSION.match(command or ""):
        return True
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return None
    procs = table() if procs is None else procs
    if pid not in procs:
        return None
    front = procs[pid][3]
    return any(claude_argv(procs[p][1]) for p in under(pid, procs) if p in procs and procs[p][2] == front)


def pid_under(top, pid, procs=None):
    """Is process `pid` process `top` itself or one under it (table())? False when either is not in the table or
    either is no process id. C-198: an adapter's mark (@altention_agent) counts only for a process of the pane."""
    try:
        top, pid = int(top), int(pid)
    except (TypeError, ValueError):
        return False
    procs = table() if procs is None else procs
    return top in procs and pid in procs and pid in under(top, procs)


SHELLS = ("bash", "zsh", "sh", "dash", "fish")


def name_of(pid):
    """The process's own name (/proc/<pid>/comm; elsewhere `ps -o comm=`), without a login shell's "-"; "" when it
    cannot be read."""
    try:
        with open("/proc/%d/comm" % int(pid)) as f:
            name = f.read().strip()
    except (OSError, ValueError):
        try:
            name = subprocess.run(["ps", "-o", "comm=", "-p", str(int(pid))], stdout=subprocess.PIPE,
                                  stderr=subprocess.DEVNULL, universal_newlines=True, timeout=TIMEOUT).stdout.strip()
        except (OSError, ValueError, subprocess.SubprocessError):
            return ""
    return os.path.basename(name).lstrip("-")


def adapter_pid(top, pid, procs=None):
    """May process `pid` stand for an adapter in the pane whose process is `top` (C-198 @altention_agent)? It is
    `top` or under it (pid_under()), and no shell (SHELLS, by its own name): a mark naming a shell would make the
    shell an adapter after the adapter ended, and messages typed into it (#code-review-2 on 6d91c5b7, manager)."""
    return pid_under(top, pid, procs) and name_of(pid) not in SHELLS


class Lazy:
    """For a listing: table() read the first time a pane needs it, then kept (one listing reads it at most once). A
    pane whose processes cannot be told goes by its name, as before C-92 b."""

    def __init__(self):
        self.procs = None

    def __call__(self, command, pid):
        if command == "claude" or CLAUDE_VERSION.match(command or ""):
            return True
        if self.procs is None:
            self.procs = table()
        said = runs_claude(command, pid, self.procs)
        return is_claude(command) if said is None else said
