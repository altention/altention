#!/usr/bin/env python3
"""Usage boards in the dashboard channel, the instance's usage.channel (hourly from cron on the machine with the `usage` role).

  python3 usage/collect.py            gather, post the boards
  python3 usage/collect.py --print    gather, print the boards instead

Two kinds of numbers:
- the subscription's limits (this 5-hour window, this week): the whole Claude account, read from the answer to one
  request on the cheapest model each round (read_limits: `claude -p`, its rate_limit_event; no screen is read);
  only when the instance's usage.subscription_limits is true (off by default);
- this Linux user's usage on each machine in plugins.usage.sources of the instance's plugins.json (usage/remote.py:
  ccusage, Claude Code and Codex, priced like the API). Not what the subscription costs, but proportional. Not set
  (what an install leaves): this machine alone, the one collecting, where usage/install.sh put ccusage; other
  machines are added by listing them (each needs usage/install-node.sh run there once).
Each run also appends a sample to ~/.local/share/usage/samples.jsonl, so weeks can be compared later.
Each round notes in ~/.cache/usage/state.json when it last posted the boards ("last_run"), why the last round that
stopped did ("last_failed": when, why; cleared by a round that ends), and the machines it could not read
("last_errors"): doctor reads them (a round that failed every hour was silent before: owner's re-install 2026-10-15).
"""
import datetime
import getpass
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
HOME_REPO = os.path.dirname(HERE)
SAMPLES = os.path.expanduser("~/.local/share/usage/samples.jsonl")


SWITCH = "usage.subscription_limits"  # true: the subscription's limits are read, by one request an hour (read_limits)


def load_settings():
    """(channel, plan, limits) from the instance's usage.channel, usage.plan and SWITCH; plan is [name, how many Pro
    allowances]; limits is True only when SWITCH is true (off by default: reading costs one request an hour, made with
    the owner's subscription). Unreadable settings or a malformed plan: (None, why), and the round is skipped (never a
    guessed channel)."""
    try:
        sys.path.insert(0, HOME_REPO)
        from altention import config
        channel, plan = config.setting("usage.channel"), config.setting("usage.plan", ["Max 20x", 20])
        limits = config.setting(SWITCH, False)
    except Exception as e:
        return None, "實例設定讀不出來（%s）" % e
    if not channel:
        return None, "實例設定沒有 usage.channel（看板頻道的 ID）"
    if not (isinstance(plan, (list, tuple)) and len(plan) == 2 and isinstance(plan[0], str)
            and isinstance(plan[1], (int, float)) and not isinstance(plan[1], bool)):
        return None, "usage.plan 要是 [方案名稱, 相當於幾個 Pro]，現在是 %r" % (plan,)
    return (str(channel), (plan[0], plan[1]), limits), None


def state_path():
    return os.path.expanduser("~/.cache/usage/state.json")


def now_iso():
    return datetime.datetime.now(datetime.timezone.utc).astimezone().isoformat(timespec="seconds")


def note(**fields):
    """Merge `fields` into state.json (None removes a key). Never raises: the boards matter more than the note."""
    path = state_path()
    try:
        with open(path) as f:
            st = json.load(f)
        st = st if isinstance(st, dict) else {}
    except (OSError, ValueError):
        st = {}
    for k, v in fields.items():
        if v is None:
            st.pop(k, None)
        else:
            st[k] = v
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path + ".tmp", "w") as f:
            json.dump(st, f, ensure_ascii=False)
        os.replace(path + ".tmp", path)
    except OSError:
        pass


def default_sources(config):
    """plugins.usage.sources when it is not set: this machine, the one collecting (the install put ccusage here)."""
    return [config.this_machine()]


def switched_off(value):
    """Why the limits are not read, for the board, when SWITCH is not true."""
    if value is False:
        return ("沒有打開。在實例的 plugins.json 設 \"usage\": {\"subscription_limits\": true} 就會讀：每小時用你的訂閱帳號"
                "跑一次 claude -p，送一句最便宜的請求（haiku），會算進 5 小時的額度")
    return "%s 要是 true 或 false，現在是 %s，當成沒打開" % (SWITCH, json.dumps(value, ensure_ascii=False))


CHANNEL, PLAN = None, ("?", 1)  # set by main() from load_settings()
WEEK = "一二三四五六日"
CLAUDE = os.path.expanduser("~/.local/bin/claude")


def inventory():
    sys.path.insert(0, HOME_REPO)
    from altention import config  # the instance's machine list, or the old one
    return config.inventory()


def machine_usage(name, m, days=31):
    """usage/remote.py on one machine (here, or over SSH with its aliases). Here: the remote.py of this very engine,
    run as it is: never a pull (in release mode that would take a tag unchecked every hour, with no restart flag for
    the bot), never the engine the list's entry names (it may be another checkout)."""
    here = os.uname().nodename.split(".")[0].lower() == name.lower() or "local" in m.get("ssh", [])
    sys.path.insert(0, HOME_REPO)
    from altention import config  # that machine's engine: its entry's "engine", else the default place
    cmd = ("cd %s && { if [ -x bin/home-pull ]; then bin/home-pull --quiet; else git pull -q --rebase --autostash; fi; } "
           ">/dev/null 2>&1; python3 usage/remote.py %d" % (config.engine_on(m), days))
    whys = []  # what each try ran into, for the board: not one word for every way it fails
    for alias in (["local"] if here else [a for a in m.get("ssh", []) if a != "local"]):
        argv = [sys.executable, os.path.join(HERE, "remote.py"), str(days)] if alias == "local" else \
            ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", alias, cmd]
        via = "" if alias == "local" else "（經 %s）" % alias
        try:
            r = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, timeout=900)
        except subprocess.TimeoutExpired:
            whys.append("超過 900 秒沒結束%s" % via)
            continue
        last = ([x.strip() for x in r.stderr.split("\n") if x.strip()] or [""])[-1][:150]
        if r.returncode == 0 and r.stdout.strip():
            try:
                data = json.loads(r.stdout)
            except ValueError:
                whys.append("remote.py 的輸出讀不懂%s" % via)
                continue
            data["machine"] = name
            return data
        if alias != "local" and r.returncode == 255:
            whys.append("連不上%s%s" % (via, "：" + last if last else ""))
        else:
            whys.append("remote.py 失敗%s（結束碼 %d%s）" % (via, r.returncode, "：" + last if last else ""))
    return {"machine": name, "error": "；".join(whys) or "機器清單裡沒有連得過去的別名"}


# The subscription's limits come with the answer to one request (K-04; measured on 2.1.299): `claude -p` with
# stream-json output gives a rate_limit_event whose rate_limit_info.unifiedWindows has five_hour and seven_day, each
# {utilization: 0..1 (more when over), resetsAt: epoch seconds}. Opening Claude Code alone gives nothing (its statusLine has no
# rate_limits before a request), so each round sends PROMPT on the cheapest model. Nothing of the owner's is loaded
# (no settings, hooks, MCP servers or tools; an empty folder of its own), and nothing is kept (no transcript).
# unifiedWindows is not in Claude Code's documentation: this reads it as measured on 2.1.299 (usage/samples/). After
# an update, measure again against that sample (REPAIR). Not measured: the answer when a limit is used up (status
# "rejected"); the request then may fail without the event, and the board says so ("請求失敗…").
PROMPT = "Reply with the single character 1."
PROBE = ["-p", "--model", "haiku", "--no-session-persistence", "--setting-sources", "project", "--strict-mcp-config",
         "--tools", "", "--output-format", "stream-json", "--verbose"]
PROBE_SECS = 120
WINDOWS = (("five_hour", "Current session"), ("seven_day", "Current week (all models)"))  # the titles samples have
MONTHS = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()
CHANGED = "格式變了"


def reset_text(ts, now, with_date, zone=None):
    """Like the /usage panel said it, in the boards' time (usage_zone.zone(); this machine's without one): "7pm",
    "Oct 19, 4:30am" (the week board groups weeks by the part before the comma, so this has to stay the shape the
    old samples have)."""
    t = zone.local(ts) if zone else datetime.datetime.fromtimestamp(ts)
    hm = "%d%s%s" % (t.hour % 12 or 12, ":%02d" % t.minute if t.minute else "", "am" if t.hour < 12 else "pm")
    if with_date or t.date() != now.date():
        return "%s %d, %s" % (MONTHS[t.month - 1], t.day, hm)
    return hm


def limits_from_stream(text, now=None, zone=None):
    """(limits, why) from `claude -p --output-format stream-json` output: limits [[title, percent, reset text]] as
    the samples keep them, or [] and why not. A shape not as measured is said, never guessed."""
    now = now or (zone.local(time.time()) if zone else datetime.datetime.now())
    info, failed = None, None
    for line in text.split("\n"):
        try:
            d = json.loads(line)
        except ValueError:
            continue
        if not isinstance(d, dict):
            continue
        if d.get("type") == "rate_limit_event" and isinstance(d.get("rate_limit_info"), dict):
            if info is None or isinstance(d["rate_limit_info"].get("unifiedWindows"), dict) or \
                    not isinstance(info.get("unifiedWindows"), dict):
                info = d["rate_limit_info"]  # the last one with the windows counts (one without does not hide it)
        elif d.get("type") == "result" and d.get("is_error"):
            failed = str(d.get("result") or d.get("subtype") or "")[:200]
    if info is None:
        if failed is not None:
            return [], "請求失敗：%s" % failed
        return [], "%s（輸出裡沒有 rate_limit_event）" % CHANGED
    windows = info.get("unifiedWindows")
    if not isinstance(windows, dict) and info.get("status") == "rejected":
        return [], "額度用完了（rate_limit_event 說 rejected，沒有各段的數字）"
    if not isinstance(windows, dict):
        return [], "%s（rate_limit_event 沒有 unifiedWindows）" % CHANGED
    out = []
    for key, title in WINDOWS:
        w = windows.get(key)
        u, at = (w.get("utilization"), w.get("resetsAt")) if isinstance(w, dict) else (None, None)
        if not all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in (u, at)) or u < 0:
            return [], "%s（unifiedWindows.%s 是 %s）" % (CHANGED, key, json.dumps(w)[:100])
        out.append([title, int(round(u * 100)), reset_text(at, now, key != "five_hour", zone)])
    return out, None


def read_limits(zone=None):
    """(limits, why): one request through `claude -p` (PROBE), its rate_limit_event read. No tmux, no screen."""
    if not os.path.exists(CLAUDE):
        return [], "找不到 claude（%s）" % CLAUDE
    sys.path.insert(0, HOME_REPO)
    from altention import background  # this Claude is a background one, like every one the engine starts for itself
    folder = tempfile.mkdtemp(prefix="usage-probe-")  # its own empty folder: no project's CLAUDE.md or settings
    try:
        r = subprocess.run([CLAUDE] + PROBE + [PROMPT], cwd=folder, env=background.env(), stdin=subprocess.DEVNULL,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, timeout=PROBE_SECS)
    except subprocess.TimeoutExpired:
        return [], "claude 沒有在 %d 秒內結束" % PROBE_SECS
    finally:
        shutil.rmtree(folder, ignore_errors=True)
    limits, why = limits_from_stream(r.stdout, zone=zone)
    if why and r.returncode:
        why += "（結束碼 %d：%s）" % (r.returncode, r.stderr.strip()[-200:])
    return limits, why


def money(x):
    return "$%.0f" % x if x >= 10 else "$%.1f" % x


def tokens(n):
    return "%.1f 億" % (n / 1e8) if n >= 1e8 else "%.0f 萬" % (n / 1e4) if n >= 1e4 else str(n)


def day_label(iso):
    d = datetime.date.fromisoformat(iso)
    return "%s（%s）" % (d.strftime("%m-%d"), WEEK[d.weekday()])


def limits_board(limits, why=None, off=False):
    if off:
        return "（不讀訂閱額度：%s）" % why
    if not limits:
        return "（這次讀不到用量額度：%s）" % (why or "不知道為什麼")
    names = {"Current session": "這 5 小時", "Current week (all models)": "這週（所有模型）"}
    rows = ["整個 Claude 帳號（包含其他 Linux 帳號和其他裝置），目前方案 %s" % PLAN[0]]
    for title, pct, reset in limits:
        label = names.get(title) or ("這週（%s）" % title.split("(", 1)[-1].rstrip(")") if "(" in title else title)
        rows.append("- %s：**%d%%**%s" % (label, pct, "　（%s 重置）" % reset if reset else ""))
    week = next((p for t, p, _ in limits if t == "Current week (all models)"), None)
    if week is not None:
        rows.append("- 這週用量換算：約 Pro 一週額度的 **%.1f 倍**、Max 5x 的 %.1f 倍（照 Max 20x = 20 倍 Pro、Max 5x = 5 倍）"
                    % (week * PLAN[1] / 100.0, week * PLAN[1] / 500.0))
    return "\n".join(rows)


def mine_board(results, today):
    rows = ["這個 Linux 帳號（%s）各機器，ccusage 照 API 價格估算（只拿來比大小）" % getpass.getuser()]
    total_day = total_week = 0.0
    for r in results:
        if r.get("error"):
            rows.append("- %s：%s" % (r["machine"], r["error"]))
            continue
        agents = sorted(set(d["agent"] for d in r["daily"])) or ["claude"]
        for a in agents:
            day = sum(d["cost"] for d in r["daily"] if d["agent"] == a and d["date"] == today)
            week = sum(d["cost"] for d in r["daily"] if d["agent"] == a and d["date"] > (
                datetime.date.fromisoformat(today) - datetime.timedelta(days=7)).isoformat())
            toks = sum(d["tokens"] for d in r["daily"] if d["agent"] == a and d["date"] == today)
            total_day, total_week = total_day + day, total_week + week
            rows.append("- %s · %s：今天 %s（%s token），近 7 天 %s" % (r["machine"], a.capitalize(), money(day), tokens(toks), money(week)))
    rows.append("- **合計：今天 %s，近 7 天 %s**" % (money(total_day), money(total_week)))
    return "\n".join(rows)


def daily_board(results, today):
    days = [(datetime.date.fromisoformat(today) - datetime.timedelta(days=i)).isoformat() for i in range(6, -1, -1)]
    rows = []
    for d in days:
        parts = [(r["machine"], sum(x["cost"] for x in r.get("daily", []) if x["date"] == d)) for r in results]
        total = sum(c for _, c in parts)
        detail = "、".join("%s %s" % (m, money(c)) for m, c in parts if c >= 0.05)
        rows.append("- %s **%s**%s" % (day_label(d), money(total), "：" + detail if detail else ""))
    return "\n".join(rows)


def projects_board(results, top=10):
    items = [(p["cost"], p["project"], r["machine"], p["agent"]) for r in results for p in r.get("projects", [])]
    items.sort(reverse=True)
    rows = ["近 7 天，依 API 等值排序"]
    for cost, name, machine, agent in items[:top]:
        if cost < 0.5:
            break
        rows.append("- %s（%s%s）%s" % (name, machine, "" if agent == "claude" else " · " + agent, money(cost)))
    return "\n".join(rows)


def weeks_board(samples):
    """The highest weekly percentage each week reached (one week ends when its reset time passes)."""
    weeks = {}
    for s in samples:
        reset = (s.get("week_reset") or "").split(",")[0]  # "Oct 5, 4:59am" and "Oct 5, 5am" are the same week
        if reset and s.get("week") is not None:
            w = weeks.setdefault(reset, {"pct": 0, "cost": 0.0})
            w["pct"] = max(w["pct"], s["week"])
            w["cost"] = max(w["cost"], s.get("cost7", 0.0))
    if not weeks:
        return "（還沒有資料，每小時記一次）"
    rows = ["每週最高的週用量（%s）和當時近 7 天的 API 等值" % PLAN[0]]
    for reset, w in list(weeks.items())[-6:]:
        rows.append("- 到 %s 重置的那週：**%d%%**（≈ Pro 的 %.1f 倍），%s" % (reset, w["pct"], w["pct"] * PLAN[1] / 100.0, money(w["cost"])))
    return "\n".join(rows)


VENV = os.path.expanduser("~/.local/share/usage/venv/bin/python")  # has matplotlib (usage/install.sh)
CHART = os.path.expanduser("~/.local/share/usage/chart.png")


def post(board, text, file=None):
    """altention dc post's exit status (0: posted)."""
    return subprocess.run([os.path.join(HOME_REPO, "bin", "altention"), "dc", "post", CHANNEL, "--board", board] + (["--file", file] if file else [])
                   + [text], check=False).returncode


def chart(today, results, samples, offset=None):
    """The charts as one PNG (None if they could not be drawn)."""
    if not os.path.exists(VENV):
        return None
    data = os.path.join(os.path.dirname(CHART), "chart-data.json")
    with open(data, "w") as f:
        json.dump({"today": today, "results": results, "samples": samples, "plan_pro": PLAN[1], "plan_name": PLAN[0], "utc_offset": offset}, f,
                  ensure_ascii=False)
    r = subprocess.run([VENV, os.path.join(HERE, "chart.py"), data, CHART], stderr=subprocess.PIPE,
                       universal_newlines=True, timeout=300)
    if r.returncode != 0:
        print("畫圖失敗：%s" % r.stderr[-500:], file=sys.stderr)
        return None
    return CHART


def main_noted(show_only=False):
    """main(), with a round that stops noted in state.json for doctor ("last_failed": when, and why)."""
    try:
        main(show_only)
    except BaseException as e:
        if isinstance(e, KeyboardInterrupt) or (isinstance(e, SystemExit) and not isinstance(e.code, str) and not e.code):
            raise
        why = str(e.code if isinstance(e, SystemExit) else "%s: %s" % (type(e).__name__, e)).strip()
        note(last_failed={"at": now_iso(), "why": (why.splitlines() or [""])[-1][:300]})
        raise


def main(show_only=False):
    global CHANNEL, PLAN
    got, why = load_settings()
    if got is None:
        print("%s usage：%s，這一輪跳過" % (time.strftime("%F %T"), why), file=sys.stderr)
        return
    CHANNEL, PLAN, limits_on = got
    try:
        inv = inventory()
    except Exception as e:  # a broken machine list: skip this round rather than write a sample of one machine
        sys.exit("usage：機器清單讀不出來，這一輪不收集（不寫樣本）：%s" % e)
    try:
        from altention import config  # (inventory() above put the engine on the path)
        sources = config.setting("plugins.usage.sources")
        if sources is None:
            sources = default_sources(config)
    except Exception as e:
        sys.exit("usage：實例設定讀不出來，這一輪不收集（不寫樣本）：%s" % e)
    if not isinstance(sources, list) or not all(isinstance(n, str) for n in sources):
        sys.exit("usage：plugins.usage.sources 要是機器名的清單，現在是 %r，這一輪不收集" % (sources,))
    if not sources:  # nothing to collect from: no sample, rather than an empty one
        sys.exit("usage：實例 plugins.json 的 plugins.usage.sources 是空的，這一輪不收集（拿掉這個鍵就收集這台）")
    sys.path.insert(0, HERE)
    import usage_zone
    zone, _, problem = usage_zone.zone()  # the instance's core.timezone, as the schedules (and remote.py's days)
    if problem:
        print("%s usage：時區設定不能用，照這台機器的時區：%s" % (time.strftime("%F %T"), problem), file=sys.stderr)
    today = zone.local(time.time()).date().isoformat()
    known = inv.get("machines") or config.machines()  # without a machine list: this machine alone
    results = [machine_usage(n, known[n]) for n in sources if n in known]
    if limits_on is not True:
        limits, why = [], switched_off(limits_on)
    else:
        try:
            limits, why = read_limits(zone)
        except Exception as e:
            limits, why = [], repr(e)
    if why and limits_on is not False:  # the cron log (plugin.json do.log): not for a plain "off"
        print("%s usage：讀不到用量額度：%s" % (time.strftime("%F %T"), why), file=sys.stderr)
    week = next(((p, r) for t, p, r in limits if t == "Current week (all models)"), (None, None))
    cost7 = sum(d["cost"] for r in results for d in r.get("daily", []) if d["date"] > (
        datetime.date.fromisoformat(today) - datetime.timedelta(days=7)).isoformat())
    os.makedirs(os.path.dirname(SAMPLES), mode=0o700, exist_ok=True)
    os.chmod(os.path.dirname(SAMPLES), 0o700)  # private in plugin.json: no other account reads the samples
    with open(SAMPLES, "a") as f:
        f.write(json.dumps({"t": time.time(), "limits": limits, "week": week[0], "week_reset": week[1],
                            "cost7": round(cost7, 2)}, ensure_ascii=False) + "\n")
    samples = []
    with open(SAMPLES) as f:
        for line in f:
            try:
                samples.append(json.loads(line))
            except ValueError:
                pass
    boards = [("訂閱額度", limits_board(limits, why, off=limits_on is not True)), ("這個帳號", mine_board(results, today)),
              ("每天", daily_board(results, today)), ("專案", projects_board(results)), ("每週", weeks_board(samples))]
    png = chart(today, results, samples, zone.offset_at(time.time()))
    unposted = []
    for name, text in boards:
        if show_only:
            print("== %s\n%s\n" % (name, text))
        else:
            if post(name, text) != 0:
                unposted.append(name)
            time.sleep(1)
    if png and show_only:
        print("圖表：%s" % png)
    elif png:
        if post("圖表", "每天的用量、這週的訂閱額度、近 7 天的專案（每小時更新）", png) != 0:
            unposted.append("圖表")
    if show_only:
        return
    errors = ["%s：%s" % (r["machine"], r["error"]) for r in results if r.get("error")]
    errors += ["%s：不在機器清單裡" % n for n in sources if n not in known]
    note(last_errors=errors or None)
    if unposted:
        sys.exit("usage：看板貼不出去（%s），樣本已經記下" % "、".join(unposted))
    note(last_run=now_iso(), last_failed=None)


if __name__ == "__main__":
    main_noted("--print" in sys.argv[1:])
