"""The route table (state/routes.json): which pane of the owner's tmux reports to which channel. Read and written
here, on the machine the panes are on: by the bot for its own machine (bot/routes.py), by tools/dcremote on
another. Nothing of Discord and nothing of the bot's settings; it came out of routes.py as it was, and replaces the
copy tools/dcremote carried (stage 3b step 1.9)."""
import json
import os
import subprocess

from altention.connectors.claude_code import sessions as cc_sessions

ROUTES = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "state", "routes.json")


def load():
    try:
        with open(ROUTES) as f:
            table = json.load(f)
        return table if isinstance(table, dict) else {}
    except (OSError, ValueError):
        return {}


def save(table):
    os.makedirs(os.path.dirname(ROUTES), exist_ok=True)
    tmp = ROUTES + ".tmp"
    with open(tmp, "w") as f:
        json.dump(table, f, indent=1)
    os.replace(tmp, ROUTES)  # the hook never reads a half-written table


def channel_of(entry):
    """A route table entry: {"channel", "pid"} (or, as first written, just the channel id)."""
    return entry if isinstance(entry, str) else (entry or {}).get("channel")


def entry(channel_id, pid):
    """A route: the channel, the pane's process, and the session it shows if it is a `claude attach` viewer."""
    out = {"channel": str(channel_id), "pid": str(pid or "")}
    sid = cc_sessions.attached_session(pid) if pid else None
    if sid:
        out["session"] = sid
    return out


def bind_key(pane_key, channel_id, pid):
    """Route the pane (by its key in the table: "socket|%id") to the channel. The pane's process id goes along:
    pane numbers start over when a tmux server restarts, and the hook checks it is still the same pane
    (tools/dchook routed())."""
    table = dict((k, v) for k, v in load().items() if channel_of(v) != str(channel_id))  # one pane per channel
    table[pane_key] = entry(channel_id, pid)
    save(table)


def unbind(channel_id):
    save(dict((k, v) for k, v in load().items() if channel_of(v) != str(channel_id)))


def project_name(cwd):
    """The project a directory belongs to: the name of its git repository (also from inside a worktree),
    or the directory's own name."""
    try:
        r = subprocess.run(["git", "-C", cwd, "rev-parse", "--path-format=absolute", "--git-common-dir"],
                           stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, universal_newlines=True, timeout=5)
        common = r.stdout.strip()
        if r.returncode == 0 and os.path.basename(common) == ".git":
            return os.path.basename(os.path.dirname(common))
    except (OSError, subprocess.SubprocessError):
        pass
    return os.path.basename(cwd.rstrip("/")) or "misc"


def without_pid(table):
    """The keys of routes written before a route carried its pane's process (just the channel id, or no "pid"):
    the hook cannot tell such a pane from another that got its number after a tmux restart. Shown by doctor."""
    return sorted(k for k, v in table.items() if not isinstance(v, dict) or not v.get("pid"))
