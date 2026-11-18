"""Who is sending (C-186 P0-2): the Claude Code a command runs under, found by walking up from this process to the
nearest Claude Code and reading that process's own environment, not this one's: a command's environment is the
caller's to set (`DCBOT_CHANNEL=… altention dc post …`), the Claude's was set by whoever started it (the bot, the
owner). Same system account, so this guards against mistakes and casual imitation, not a program set on forging.

Claude Code is told by its command line (process.claude_argv: the native `claude`, macOS's versioned file, or node
running Claude's script), not by the name the system gives the process.
Linux: /proc/<pid>/stat (the parent after the last ")": a command name may hold spaces and brackets), cmdline,
environ. macOS (no /proc): `ps -o ppid=,args=` and `ps eww -o command=` (its environment after the command line)."""
import base64
import json
import os
import re
import subprocess

from altention.connectors.claude_code.process import claude_argv

KEYS = ("DCBOT_CHANNEL", "TMUX", "TMUX_PANE")  # all that is read of the Claude's environment
ENV = "ALTENTION_SENDER"  # dcctl handed to the bot's machine (botside.hand_over): who it is, looked up where it ran
MOST = 2048  # ENV's length at most
DEPTH = 40  # parents looked at, at most


def _linux(pid, proc):
    """(parent pid, words of its command line, its environment()) of `pid`, or None."""
    try:
        with open(os.path.join(proc, str(pid), "stat"), "rb") as f:
            stat = f.read().decode("utf-8", "replace")
        with open(os.path.join(proc, str(pid), "cmdline"), "rb") as f:
            argv = [w.decode("utf-8", "replace") for w in f.read().split(b"\0") if w]
    except OSError:
        return None
    rest = stat[stat.rfind(")") + 1:].split()
    try:
        ppid = int(rest[1])
    except (IndexError, ValueError):
        return None

    def env():
        try:
            with open(os.path.join(proc, str(pid), "environ"), "rb") as f:
                raw = f.read().decode("utf-8", "replace")
        except OSError:
            return None
        out = {}
        for item in raw.split("\0"):
            k, sep, v = item.partition("=")
            if sep and k in KEYS:
                out[k] = v
        return out
    return ppid, argv, env


def _mac(pid, run):
    try:
        r = run(["ps", "-o", "ppid=,args=", "-p", str(pid)], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                universal_newlines=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    m = re.match(r"\s*(\d+)\s+(.*)$", (r.stdout or "").strip())
    if r.returncode != 0 or not m:
        return None

    def env():
        try:
            e = run(["ps", "eww", "-o", "command=", "-p", str(pid)], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                    universal_newlines=True, timeout=5)
        except (OSError, subprocess.SubprocessError):
            return None
        if e.returncode != 0:
            return None
        return {k: v for k, v in re.findall(r"(?:^|\s)(%s)=(\S*)" % "|".join(KEYS), e.stdout or "")}
    return int(m.group(1)), m.group(2).split(), env


def claude_env(start=None, proc="/proc", run=subprocess.run):
    """The environment (KEYS only) of the nearest Claude Code above `start` (default: this process's parent), {} when
    that Claude's has none of them; None when there is no Claude Code above it, or it cannot be read."""
    pid = os.getppid() if start is None else start
    on_linux = os.path.isdir(os.path.join(proc, "1"))
    for _ in range(DEPTH):
        if pid <= 1:
            return None
        got = _linux(pid, proc) if on_linux else _mac(pid, run)
        if got is None:
            return None
        ppid, argv, env = got
        if claude_argv(argv):
            return env()
        pid = ppid
    return None


def describe(claude, host, cwd, machine=None):
    """Who runs a command, from claude_env()'s answer (`claude`; None: no Claude Code above it): {"machine" (the
    list's name, `host` when not given), "host", "folder" (the last part of `cwd`), "claude" (a Claude Code above
    it), "channel" (its DCBOT_CHANNEL), "pane" ("<host>|<tmux socket>|%<pane>", for the bot to look its channel up)}.
    dcctl turns it into a key (bot/senders.py, C-172a P1d)."""
    out = {"machine": machine or host, "host": host, "folder": os.path.basename(cwd.rstrip("/")) or cwd,
           "claude": claude is not None}
    claude = claude or {}
    if str(claude.get("DCBOT_CHANNEL") or "").isdigit():
        out["channel"] = claude["DCBOT_CHANNEL"]
    elif claude.get("TMUX") and claude.get("TMUX_PANE"):
        out["pane"] = "%s|%s|%s" % (host, claude["TMUX"].split(",")[0], claude["TMUX_PANE"])
    return out


def encoded(desc):
    """describe()'s answer as one word a shell takes as it is (ENV's value on the ssh command line)."""
    return base64.urlsafe_b64encode(json.dumps(desc, ensure_ascii=False).encode("utf-8")).decode("ascii")


def decoded(word):
    """encoded()'s word back, or None when it is missing, too long or not what encoded() makes."""
    if not word or len(word) > MOST:
        return None
    try:
        got = json.loads(base64.urlsafe_b64decode(word.encode("ascii")).decode("utf-8"))
    except (ValueError, UnicodeError):
        return None
    if not isinstance(got, dict) or not all(isinstance(got.get(k), str) and got.get(k) for k in ("machine", "folder")):
        return None
    if any(k in got and not isinstance(got[k], str) for k in ("host", "channel", "pane")):
        return None
    return dict(got, claude=bool(got.get("claude")))
