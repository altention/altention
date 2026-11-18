#!/usr/bin/env python3
"""This machine's coding-agent usage for this Linux user, as JSON (standard library; Python 3.8 is enough).

  python3 usage/remote.py [DAYS]     the last DAYS days (default 8), dates in the instance's core.timezone (usage_zone.py; else this machine's); projects: the last 7

ccusage (usage/install-node.sh) reads the local Claude Code and Codex logs and prices every request like the
API: the dollar figures are API-equivalent, not what the subscription costs, but they grow with it. Sessions
are named after their project here, where the logs are: Claude Code keeps a session as
~/.claude/projects/<folder>/<id>.jsonl, Codex writes its folder into the session's first line.
Output: {"machine", "daily": [{date, agent, cost, tokens}], "projects": [{agent, project, cost, tokens, last}],
"sessions": [{agent, id, cwd, project, cost1, cost7, cost30, costAll, tokens1, tokens7, tokens30, tokensAll, last}],
"since": the first day ccusage has any usage of (the cumulative counts from it)}. 1 is today (in the zone), 7 and 30
end today; All is a conversation's whole cost, so every conversation ccusage still sees is listed (cost30, tokens30:
None when DAYS < 30). How far back "All" goes: as far as Claude Code keeps its transcripts, cleanupPeriodDays in
~/.claude/settings.json, 30 days when not set, rolling forward.

ccusage's session report gives a conversation's whole cost, for every conversation used since --since (C-98: the
projects board's 7 days counted a long one with everything it ever cost). So each Claude Code conversation's cost
is split over the days by what its transcripts used each day (tokens weighted as priced: input 1, output 5, cache
written 1.25 or 2 for an hour, cache read 0.1), then each day is scaled to ccusage's own total for that day. A
conversation with no usage rows (Codex, a workflow's folder) counts on the day it was last active.
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


WEIGHTS = (("input_tokens", 1.0), ("output_tokens", 5.0), ("cache_creation_input_tokens", 1.25),
           ("cache_read_input_tokens", 0.1))
HOUR_CACHE = 0.75  # an hour's cache write costs 2, not 1.25: the part of it above the 5-minute price


def stamp_of(text):
    """Epoch seconds of an ISO time ("2026-10-14T08:06:49.547Z"), or None."""
    try:
        return datetime.datetime.strptime(text[:19], "%Y-%m-%dT%H:%M:%S").replace(
            tzinfo=datetime.timezone.utc).timestamp()
    except (TypeError, ValueError):
        return None


def claude_days(z, since):
    """{session id: {date: weight}} of every Claude Code conversation whose transcript was written to since `since`
    (epoch seconds): all of its rows, older days too (else a long one's whole cost would land on its last days), its
    subagents' with it (their rows carry their parent's sessionId, under <folder>/<session id>/). A message is counted
    once (a streamed answer repeats it), by message id and request id."""
    paths, active = [], set()
    for root, _, files in os.walk(os.path.expanduser("~/.claude/projects")):
        for n in files:
            if n.endswith(".jsonl"):
                path = os.path.join(root, n)
                try:
                    recent = os.path.getmtime(path) >= since
                except OSError:
                    continue
                paths.append((path, recent))
                if recent:
                    active.add(n[:-len(".jsonl")])
    out, seen = {}, set()
    for path, recent in paths:
        if not recent and not active & set(path.split(os.sep)):
            continue
        try:
            f = open(path, encoding="utf-8", errors="replace")
        except OSError:
            continue
        with f:
            for line in f:
                if '"usage"' not in line or '"assistant"' not in line:
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                msg = row.get("message") if isinstance(row, dict) else None
                use = msg.get("usage") if isinstance(msg, dict) else None
                if row.get("type") != "assistant" or not isinstance(use, dict) or not row.get("sessionId"):
                    continue
                key = (msg.get("id"), row.get("requestId"))
                if key[0]:
                    if key in seen:
                        continue
                    seen.add(key)
                t = stamp_of(row.get("timestamp"))
                if t is None:
                    continue
                cache = use.get("cache_creation") if isinstance(use.get("cache_creation"), dict) else {}
                weight = sum(float(use.get(k) or 0) * w for k, w in WEIGHTS) + \
                    float(cache.get("ephemeral_1h_input_tokens") or 0) * HOUR_CACHE
                day = z.local(t).date().isoformat()
                days = out.setdefault(row["sessionId"], {})
                days[day] = days.get(day, 0.0) + weight
    return out


def split(sessions, weights, daily, z):
    """{session id: {date: cost}}: each one's cost over its days (weights), every day then scaled to ccusage's own
    claude total that day; one without weights on the day it was last active."""
    out, sums = {}, {}
    for s in sessions:
        sid, cost = s.get("period") or "?", float(s.get("totalCost") or 0)
        w = weights.get(sid) if s.get("agent", "claude") == "claude" else None
        total = sum(w.values()) if w else 0
        if total > 0:
            out[sid] = dict((d, cost * x / total) for d, x in w.items())
            for d, c in out[sid].items():
                sums[d] = sums.get(d, 0.0) + c
        else:
            t = stamp_of((s.get("metadata") or {}).get("lastActivity"))
            out[sid] = {(z.local(t).date().isoformat() if t else "?"): cost}
    real = {}
    for row in daily:
        if row["agent"] == "claude":
            real[row["date"]] = real.get(row["date"], 0.0) + float(row["cost"] or 0)
    for s in sessions:
        sid = s.get("period") or "?"
        if weights.get(sid) and s.get("agent", "claude") == "claude":
            out[sid] = dict((d, c * real[d] / sums[d] if d in real and sums.get(d) else c) for d, c in out[sid].items())
    return out


def share(tokens, part, cost):
    """The tokens of `part` of a conversation's cost (None when part is None)."""
    if part is None:
        return None
    return int(round(tokens * part / cost)) if cost else 0


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
    first = today - datetime.timedelta(days=days - 1)
    rows = ccusage("session", zone=zname).get("session", [])  # every one, for the cumulative
    since = min((r.get("period") for r in ccusage("daily", zone=zname).get("daily", []) if r.get("period")), default=None)
    start = time.mktime(first.timetuple()) - 86400  # a day early: the zone's midnight may come before this one's
    costs = split(rows, claude_days(z, start), daily, z)
    week, month = (today - datetime.timedelta(days=6)).isoformat(), (today - datetime.timedelta(days=29)).isoformat()
    sessions, projects = [], {}
    for s in rows:
        sid = s.get("period") or "?"
        cwd = codex.get(sid) or (cwd_of(files[sid]) if sid in files else None)
        if sid not in names:
            names[sid] = short(cwd)
        by_day, cost, toks = costs.get(sid, {}), float(s.get("totalCost") or 0), s.get("totalTokens") or 0
        c1 = sum(c for d, c in by_day.items() if d == today.isoformat())
        c7 = sum(c for d, c in by_day.items() if d >= week)
        c30 = sum(c for d, c in by_day.items() if d >= month) if days >= 30 else None
        last = (s.get("metadata") or {}).get("lastActivity") or ""
        sessions.append({"agent": s.get("agent", "?"), "id": sid, "cwd": cwd, "project": names[sid], "cost1": c1,
                         "cost7": c7, "cost30": c30, "costAll": cost, "tokens1": share(toks, c1, cost),
                         "tokens7": share(toks, c7, cost), "tokens30": share(toks, c30, cost), "tokensAll": toks,
                         "last": last})
        if c7 <= 0:
            continue
        key = (s.get("agent", "?"), names[sid])
        p = projects.setdefault(key, {"agent": key[0], "project": names[sid], "cost": 0.0, "tokens": 0, "last": ""})
        p["cost"] += c7
        p["tokens"] += share(toks, c7, cost)
        p["last"] = max(p["last"], last)
    print(json.dumps({"machine": socket.gethostname().split(".")[0], "daily": daily,
                      "projects": sorted(projects.values(), key=lambda p: -p["cost"]),
                      "sessions": sorted(sessions, key=lambda p: -p["cost7"]), "since": since}, ensure_ascii=False))


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 8)
