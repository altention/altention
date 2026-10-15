#!/usr/bin/env python3
"""This machine's coding-agent usage for this Linux user, as JSON (standard library; Python 3.8 is enough).

  python3 usage/remote.py [DAYS]     the last DAYS days (default 8), dates in the instance's core.timezone (usage_zone.py; else this machine's); projects: the last 7

ccusage (usage/install-node.sh) reads the local Claude Code and Codex logs and prices every request like the
API: the dollar figures are API-equivalent, not what the subscription costs, but they grow with it. Sessions
are named after their project here, where the logs are: Claude Code keeps a session as
~/.claude/projects/<folder>/<id>.jsonl, Codex writes its folder into the session's first line.
Output: {"machine", "daily": [{date, agent, cost, tokens}], "projects": [{agent, project, cost, tokens, last}]}.
"""
import datetime
import glob
import json
import os
import socket
import subprocess
import sys
import time


def ccusage(*args, zone=None):
    bins = sorted(glob.glob(os.path.expanduser("~/.local/opt/node-*/bin")))
    if not bins:
        raise SystemExit("沒有 ccusage：先跑 usage/install-node.sh")
    env = dict(os.environ, PATH=bins[-1] + os.pathsep + os.environ.get("PATH", ""))
    # the setting's zone, else no -z: ccusage then takes this machine's (measured); collect.py's today goes the same way
    out = subprocess.run(["ccusage"] + list(args) + ["--json"] + (["-z", zone] if zone else []), stdout=subprocess.PIPE,
                         stderr=subprocess.DEVNULL, env=env, universal_newlines=True, timeout=600).stdout
    return json.loads(out or "{}")


def claude_files():
    """{session id: its transcript}, subagent and workflow transcripts included (ccusage counts them apart)."""
    out = {}
    for root, _, files in os.walk(os.path.expanduser("~/.claude/projects")):
        for n in files:
            if n.endswith(".jsonl"):
                out.setdefault(n[:-len(".jsonl")], os.path.join(root, n))
                if n.startswith("agent-"):  # a workflow: counted by its folder
                    out.setdefault(os.path.basename(root), os.path.join(root, n))
    return out


def cwd_of(path):
    """The folder a Claude Code session worked in: the first "cwd" in its transcript."""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for i, line in enumerate(f):
                if '"cwd"' in line:
                    cwd = json.loads(line).get("cwd")
                    if cwd:
                        return cwd
                if i > 200:
                    break
    except (OSError, ValueError):
        pass
    return None


def codex_projects():
    out = {}
    for path in glob.glob(os.path.expanduser("~/.codex/sessions/*/*/*/*.jsonl")):
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                meta = json.loads(f.readline()).get("payload") or {}
        except (OSError, ValueError):
            continue
        sid = meta.get("id") or meta.get("session_id")
        if sid and meta.get("cwd"):
            out[sid] = meta["cwd"]
    return out


def short(cwd):
    """A readable project name: the folder's name, or its git repository's when it is inside one."""
    if not cwd:
        return "?"
    if cwd.startswith(("/tmp/", "/private/tmp/", "/var/folders/")):
        return "（暫存資料夾）"
    path = cwd.rstrip("/")
    while path and path != "/":
        if os.path.exists(os.path.join(path, ".git")):
            return os.path.basename(path)
        path = os.path.dirname(path)
    return os.path.basename(cwd.rstrip("/")) or "~"


def main(days):
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import usage_zone
    z, zname, _ = usage_zone.zone()  # a setting that cannot be used: this machine's (collect.py says it in the cron log)
    today = z.local(time.time()).date()
    since = (today - datetime.timedelta(days=days - 1)).strftime("%Y%m%d")
    daily = []
    for row in ccusage("daily", "--since", since, "--by-agent", zone=zname).get("daily", []):
        parts = row.get("agents") or row.get("agentBreakdowns") or None
        if isinstance(parts, list) and parts:
            for a in parts:
                daily.append({"date": row["period"], "agent": a.get("agent", "?"), "cost": a.get("totalCost", 0),
                              "tokens": a.get("totalTokens", 0)})
        else:
            for agent in (row.get("metadata") or {}).get("agents") or [row.get("agent", "?")]:
                daily.append({"date": row["period"], "agent": agent, "cost": row.get("totalCost", 0),
                              "tokens": row.get("totalTokens", 0)})
                break
    files, codex, names = claude_files(), codex_projects(), {}
    projects = {}
    week = (today - datetime.timedelta(days=6)).strftime("%Y%m%d")  # projects: the last 7 days
    for s in ccusage("session", "--since", week, zone=zname).get("session", []):
        sid = s.get("period") or "?"
        if sid not in names:
            names[sid] = short(codex.get(sid) or (cwd_of(files[sid]) if sid in files else None))
        name = names[sid]
        key = (s.get("agent", "?"), name)
        p = projects.setdefault(key, {"agent": key[0], "project": name, "cost": 0.0, "tokens": 0, "last": ""})
        p["cost"] += s.get("totalCost") or 0
        p["tokens"] += s.get("totalTokens") or 0
        p["last"] = max(p["last"], (s.get("metadata") or {}).get("lastActivity") or "")
    print(json.dumps({"machine": socket.gethostname().split(".")[0], "daily": daily,
                      "projects": sorted(projects.values(), key=lambda p: -p["cost"])}, ensure_ascii=False))


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 8)
