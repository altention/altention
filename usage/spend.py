#!/usr/bin/env python3
"""What was spent today, in the last 7 and 30 days and in all, per working folder, machine and bot channel: read only
(C-98).

  python3 usage/spend.py           as text
  python3 usage/spend.py --json    {"v": 1, ...} for programs (a plugin declares "usage.spend": 1 in its uses)
  python3 usage/spend.py --json --v 2   v1 and each day's spend, the limits' history and the plan ("usage.spend": 2)

It reads what usage/collect.py left in ~/.local/share/usage/spend.json the last round (hourly, on the machine
collecting): nothing is asked of another machine, nothing is written. Dollars are API-equivalent, as on the boards.
"Today" is the day in the instance's core.timezone. "All" goes back as far as each machine still keeps Claude Code's
transcripts: cleanupPeriodDays in ~/.claude/settings.json, which is 30 days when it is not set, so on such an
install the cumulative covers about the last 30 days and moves forward with them. "since" says from which day on
each machine, "since_all" from which day every machine is in (the latest of them).
Channels come from the bot's state on this machine (dcbot/state/channels.json, read only; C-93a's read-only command
replaces this): a conversation is matched by its id (the channel's session_id, history and the transcripts its turns
were in, on the machine its pane or turns name), never by folder (dozens of channels share the engine's). What no
channel holds is "unmatched".

--json: {"v": 1, "at": <when the data was collected, epoch seconds> or null, "today", "windows": ["1", "7", "30",
 "all"], "since": {"<machine>": "YYYY-MM-DD"}, "since_all": "YYYY-MM-DD" or null,
 "machines": [{"machine", "error"?, "old"?}], "folders": [{"machine", "cwd", "project", "agent",
 "cost": {"1", "7", "30", "all"}, "tokens": {...}, "last"}], "channels": [{"channel", "cwd", "machines", "sessions",
 "cost", "tokens"}] or null, "channels_why": <why null>, "unmatched": {"cost", "tokens"}, "error"?: <no data at all>}.
"1" is today. "30" is null for a machine that answered fewer days. Sorted by the 7 days, most first.
 "limits": the subscription's, as the last hourly sample read them (the whole account, not one machine):
 {"at": <that sample's time>, "week": {"used": <percent, may pass 100>, "resets_at": <epoch seconds> or null},
 "session": {...} (the 5-hour window) or null}, or null with "limits_why": "off" (usage.subscription_limits is
 false, or #setup is not done), "none" (no sample yet) or "unread" (the last round could not read them).
 resets_at is null in samples from before C-98.
--v 2 (usage.spend v2; without it the answer is v1, word for word): "v": 2, everything v1 has, and
 "days": [{"date", "machines": {"<machine>": <dollars>}}] the last DAYS days to "today", oldest first: a machine is
 left out of a day before its "since" and when it did not answer this round; null with "days_why": "old" when the
 data is from a round before v2 (an hour at most);
 "limits_history": {"week": [[<epoch seconds>, <percent>]], "session": [...]} the hourly samples of the last
 HISTORY_DAYS days by time (HISTORY_MAX at most, the newest), [] for a window the samples do not have, null when the
 limits are off;
 "plan": {"name", "pro": how many Pro allowances a week} from usage.plan, or null when it is not set.

Exit status: 0; 1 no data yet (said). Standard library only, Python 3.8.
"""
import datetime
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ENGINE = os.path.dirname(HERE)
WINDOWS = ("1", "7", "30", "all")
DAYS = 30           # --v 2's "days"
HISTORY_DAYS = 28   # --v 2's "limits_history": hourly, 672 samples
HISTORY_MAX = 2000
FIELD = {"1": "1", "7": "7", "30": "30", "all": "All"}  # remote.py's cost1, cost7, cost30, costAll


def text(key, **fields):
    sys.path.insert(0, ENGINE)
    from altention import text as alt_text
    return alt_text.t(key, **fields)


def read_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


NAMES = {"five_hour": "session", "seven_day": "week"}  # --json's names for collect.py's WINDOWS


def limit_titles():
    """[(--json name, the sample's title, the window's key)]: collect.py's own WINDOWS, never a copy of it."""
    sys.path.insert(0, HERE)
    import collect
    return [(NAMES[key], title, key) for key, title in collect.WINDOWS]


def samples_path():
    return os.path.expanduser("~/.local/share/usage/samples.jsonl")


def read_samples():
    """Every readable line of the samples, in the file's order ([] when there is none)."""
    try:
        with open(samples_path(), encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except OSError:
        return []
    out = []
    for line in lines:
        try:
            d = json.loads(line)
        except ValueError:
            continue
        if isinstance(d, dict):
            out.append(d)
    return out


def last_sample():
    """The last readable line of the samples, or None."""
    s = read_samples()
    return s[-1] if s else None


def limits_off():
    try:
        sys.path.insert(0, ENGINE)
        from altention import config
        return config.setting("usage.subscription_limits", True) is not True or config.setting("setup.done", True) is False
    except Exception:
        return False


def limits(sample, off):
    """(limits or None, why)."""
    if off:
        return None, "off"
    if sample is None:
        return None, "none"
    rows = dict((r[0], r) for r in sample.get("limits") or [] if isinstance(r, list) and len(r) >= 2)
    resets = sample.get("resets") if isinstance(sample.get("resets"), dict) else {}
    out = {"at": sample.get("t")}
    for name, title, key in limit_titles():
        r = rows.get(title)
        at = resets.get(key)
        out[name] = {"used": r[1], "resets_at": at if isinstance(at, (int, float)) else None} if r else None
    if out["week"] is None and out["session"] is None:
        return None, "unread"
    return out, None


def number(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def days(data, since):
    """--v 2's (days, why): each machine's dollars a day from spend.json's "daily" (collect.py's, v2 on)."""
    kept = [m for m in data.get("machines") or [] if isinstance(m, dict) and isinstance(m.get("machine"), str)
            and isinstance(m.get("daily"), list)]
    if not kept and any(isinstance(m, dict) and isinstance(m.get("sessions"), list) for m in data.get("machines") or []):
        return None, "old"
    try:
        last = datetime.date.fromisoformat(data.get("today"))
    except (TypeError, ValueError):
        return None, "old"
    dates = [(last - datetime.timedelta(days=i)).isoformat() for i in range(DAYS - 1, -1, -1)]
    out = [{"date": d, "machines": {}} for d in dates]
    for m in kept:
        spent = dict((r[0], r[1]) for r in m["daily"] if isinstance(r, list) and len(r) == 2 and number(r[1]))
        for row in out:
            if row["date"] >= (since.get(m["machine"]) or ""):
                row["machines"][m["machine"]] = round(spent.get(row["date"], 0.0), 4)
    return out, None


def limits_history(samples, off, now):
    """--v 2's "limits_history", or None when the limits are off."""
    if off:
        return None
    first = now - HISTORY_DAYS * 86400
    out = dict((name, []) for name, _, _ in limit_titles())
    for s in samples:
        if not number(s.get("t")) or s["t"] < first:
            continue
        rows = dict((r[0], r) for r in s.get("limits") or [] if isinstance(r, list) and len(r) >= 2 and number(r[1]))
        for name, title, _ in limit_titles():
            if title in rows:
                out[name].append([int(s["t"]), rows[title][1]])
    return dict((k, sorted(v)[-HISTORY_MAX:]) for k, v in out.items())


def plan():
    """usage.plan as {"name", "pro"}, or None when it is not set or not [name, number]."""
    try:
        sys.path.insert(0, ENGINE)
        from altention import config
        p = config.setting("usage.plan")
    except Exception:
        return None
    if isinstance(p, list) and len(p) == 2 and isinstance(p[0], str) and number(p[1]) and p[1] > 0:
        return {"name": p[0], "pro": p[1]}
    return None


def spend_path():
    return os.path.expanduser("~/.local/share/usage/spend.json")


def bot_state():
    return os.path.join(ENGINE, "dcbot", "state")


def this_machine():
    try:
        sys.path.insert(0, ENGINE)
        from altention import config
        return config.this_machine()
    except Exception:
        return None


def owners(channels, here):
    """{(machine, session id): channel id}. A channel's current conversation wins over one it had before."""
    current, before = {}, {}
    for cid, st in (channels.items() if isinstance(channels, dict) else ()):
        if not isinstance(st, dict) or st.get("kind") not in (None, "conversation"):
            continue
        pane = st.get("pane") if isinstance(st.get("pane"), dict) else {}
        host = pane.get("host") if isinstance(pane.get("host"), str) and pane.get("host") else here
        if isinstance(st.get("session_id"), str) and st["session_id"]:
            current[(host, st["session_id"])] = str(cid)
        for sid in st.get("history") if isinstance(st.get("history"), list) else ():
            if isinstance(sid, str):
                before.setdefault((host, sid), str(cid))
        hosts = st.get("turn_hosts") if isinstance(st.get("turn_hosts"), dict) else {}
        for turn, where in (st.get("turns").items() if isinstance(st.get("turns"), dict) else ()):
            path = where[0] if isinstance(where, list) and where and isinstance(where[0], str) else None
            if path and path.endswith(".jsonl"):
                on = hosts.get(turn) if isinstance(hosts.get(turn), str) else host
                before.setdefault((on, os.path.basename(path)[:-len(".jsonl")]), str(cid))
    before.update(current)
    return before


def add(into, s):
    for w in WINDOWS:
        c, t = s.get("cost" + FIELD[w]), s.get("tokens" + FIELD[w])
        if c is None or into["cost"][w] is None:
            into["cost"][w] = into["tokens"][w] = None
            continue
        into["cost"][w] += float(c)
        into["tokens"][w] += int(t or 0)


def empty():
    return {"cost": dict((w, 0.0) for w in WINDOWS), "tokens": dict((w, 0) for w in WINDOWS)}


def order(row):
    return tuple(-(row["cost"][w] or 0) for w in ("7", "30", "all")) + (row.get("machine") or "", row.get("cwd") or "",
                                                                         row.get("channel") or "")


def report(data, channels, here, state_there):
    """The --json answer from spend.json's data and the bot's channels (None: not read)."""
    out = {"v": 1, "at": data.get("at"), "today": data.get("today"), "windows": list(WINDOWS), "since": {},
           "since_all": None, "machines": [], "folders": [], "channels": None, "unmatched": empty()}
    folders = {}
    sessions = []
    for m in data.get("machines") or []:
        if not isinstance(m, dict) or not isinstance(m.get("machine"), str):
            continue
        out["machines"].append(dict((k, m[k]) for k in ("machine", "error", "old") if k in m))
        if isinstance(m.get("since"), str):
            out["since"][m["machine"]] = m["since"]
        for s in m.get("sessions") or []:
            if not isinstance(s, dict):
                continue
            sessions.append((m["machine"], s))
            key = (m["machine"], s.get("cwd"), s.get("agent"))
            f = folders.get(key)
            if f is None:
                f = folders[key] = dict(empty(), machine=m["machine"], cwd=s.get("cwd"), project=s.get("project"),
                                        agent=s.get("agent"), last="")
            add(f, s)
            f["last"] = max(f["last"], s.get("last") or "")
    out["since_all"] = max(out["since"].values()) if out["since"] else None
    out["folders"] = sorted((f for f in folders.values() if any(f["cost"].values())), key=order)
    if not isinstance(channels, dict):
        out["channels_why"] = text("spend.why_unreadable" if state_there else "spend.why_elsewhere")
        for _, s in sessions:
            add(out["unmatched"], s)
        return out
    who = owners(channels, here)
    rows = {}
    for machine, s in sessions:
        cid = who.get((machine, s.get("id")))
        if cid is None:
            add(out["unmatched"], s)
            continue
        r = rows.get(cid)
        if r is None:
            r = rows[cid] = dict(empty(), channel=cid, cwd=(channels.get(cid) or {}).get("cwd"), machines=[], sessions=0)
        add(r, s)
        r["sessions"] += 1
        if machine not in r["machines"]:
            r["machines"].append(machine)
    out["channels"] = sorted((r for r in rows.values() if any(r["cost"].values())), key=order)
    return out


def money(x):
    return "—" if x is None else "$%.2f" % x


def age(at, now):
    mins = max(0, int((now - at) // 60))
    return text("spend.minutes", n=mins) if mins < 120 else text("spend.hours", n=mins // 60)


def costs(row):
    return {"cost1": money(row["cost"]["1"]), "cost7": money(row["cost"]["7"]), "cost30": money(row["cost"]["30"]),
            "costall": money(row["cost"]["all"])}


def show(out, now, top=15):
    lines = [text("spend.header", age=age(out["at"], now))]
    if out["since"]:
        lines.append(text("spend.since", day=out["since_all"],
                          each="、".join("%s %s" % (m, d) for m, d in sorted(out["since"].items()))))
    for m in out["machines"]:
        if m.get("error"):
            lines.append(text("spend.machine_error", machine=m["machine"], error=m["error"]))
        elif m.get("old"):
            lines.append(text("spend.old", machine=m["machine"]))
    lines.append(text("spend.folders"))
    for f in out["folders"][:top]:
        lines.append(text("spend.row", name=f["cwd"] or f["project"] or "?", machine=f["machine"], **costs(f)))
    if out["channels"] is None:
        lines.append(text("spend.no_channels", why=out["channels_why"]))
    else:
        lines.append(text("spend.channels"))
        for r in out["channels"][:top]:
            lines.append(text("spend.row", name="<#%s>" % r["channel"], machine="、".join(r["machines"]), **costs(r)))
        lines.append(text("spend.unmatched", **costs(out["unmatched"])))
    return "\n".join(lines)


def main(argv):
    if "-h" in argv or "--help" in argv:  # spec §11.1 (C-150): says how, does nothing
        print(__doc__)
        return 0
    as_json = "--json" in argv
    rest = [a for a in argv if a != "--json"]
    v2 = rest == ["--v", "2"] and as_json
    if rest and not v2:
        print(__doc__.split("\n\n")[1])
        return 2
    data = read_json(spend_path())
    if not isinstance(data, dict) or data.get("v") != 1:
        why = text("spend.no_data")
        print(json.dumps({"v": 2 if v2 else 1, "at": None, "error": why}, ensure_ascii=False) if as_json else why)
        return 1
    state = bot_state()
    channels = read_json(os.path.join(state, "channels.json"))
    out = report(data, channels, this_machine(), os.path.isdir(state))
    samples = read_samples()
    off = limits_off()
    out["limits"], why = limits(samples[-1] if samples else None, off)
    if why:
        out["limits_why"] = why
    if v2:
        out["v"] = 2
        out["days"], why = days(data, out["since"])
        if why:
            out["days_why"] = why
        out["limits_history"] = limits_history(samples, off, time.time())
        out["plan"] = plan()
    print(json.dumps(out, ensure_ascii=False) if as_json else show(out, time.time()))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
