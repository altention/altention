#!/usr/bin/env python3
"""The cron entry of the scheduler in its record-only mode (3a.11): once a minute, one round of
scheduler.record_only() over the schedules of the plugins that run on this machine (their plugin.json "schedules"
and the schedule files they name). Nothing is ever run, posted or woken: what would run goes into the scheduler's
would-run log, problems into its problems log and this one, and doctor shows them. Nobody is told while it only
records (manager 2026-10-07: nothing depends on it yet; "told after 5 rounds" comes with the switch to running).

After the round is done and marked, the traces the items name (scheduler: "trace") are sampled in a child process
(sample_round: at most SAMPLE_LIMIT seconds, never two at once) into trace-history, so --compare can check every
scheduled time, items sharing a trace each their own (manager 2026-10-08).

From a checkout that is not this machine's engine (engine_id "other") nothing is recorded or written at all, as with
3a.10's lines (manager 2026-10-07). When that cannot be told (engine_id "unknown": a machine list that cannot be
read, a .git out of reach) nothing is recorded either, and that is said once an hour in cron-schedule.log.
Nothing it does reaches cron's own output: stdout and stderr are taken over for the round (also what any module
or child writes there) and said through to_say()/say(), bounded (manager 2026-10-07). Standard library only."""
import json
import os
import sys
import time

ENGINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ENGINE)
config = engine_id = plugin = scheduler = None  # loaded by load(), inside main()'s taking over of the output
BROKEN = None
REFUSED = False  # another checkout: nothing at all is written, not even what turned up on the output


def load():
    """The engine's own modules, loaded once main() holds stdout and stderr (whatever they say at import is caught
    too). If one cannot load, BROKEN says why: a bounded line (say() needs none of them), never a traceback."""
    global config, engine_id, plugin, scheduler, BROKEN
    try:
        from altention import config, engine_id, plugin, scheduler  # noqa: F811
        BROKEN = None
    except Exception as e:
        BROKEN = "%s: %s" % (type(e).__name__, e)
    return BROKEN

LOG_MAX = 256 * 1024  # bytes: cron-schedule.log is renamed to .1 beyond this (a round with something to say is one line)


def say(text):
    """One line in cron-schedule.log, renamed to .1 first when it is past LOG_MAX; never raises."""
    path = os.path.expanduser("~/.local/state/altention/cron-schedule.log")
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        try:
            if os.path.getsize(path) > LOG_MAX:
                os.replace(path, path + ".1")
        except OSError:
            pass
        with open(path, "a", encoding="utf-8") as f:
            f.write("%s %s\n" % (time.strftime("%Y-%m-%dT%H:%M:%S%z"), " ".join(str(text).split())))
    except OSError:
        pass


AGAIN = 3600  # seconds: a problem that goes on is said again this often
ONCE = "在不在這台跑不知道"  # a plugin's place not known (no bot yet): said when it starts, not again while it lasts


def to_say(machine, problems, now):
    """The problems of this round to write down: those not said within AGAIN seconds (their "連續第 N 輪" counts
    left out when compared; one with ONCE in it only when it starts), and a line when the last ones are gone. Kept
    beside the scheduler's state; never raises (then: all of them)."""
    import re
    path = os.path.join(scheduler.state_dir(), "said-dry.%s.json" % re.sub(r"[/\\\0]", "_", machine))
    said = config.read_json(path)
    said = said if isinstance(said, dict) else {}
    keep, out = {}, []
    for p in problems:
        key = re.sub(r"連續第 \d+ 輪", "", p)
        at = said.get(key)
        if isinstance(at, (int, float)) and (now - at < AGAIN or ONCE in key):
            keep[key] = at
        else:
            keep[key], out = now, out + [p]
    if said and not problems:
        out = ["（上一輪的問題都沒了）"]
    if keep != said:
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path + ".tmp", "w") as f:
                json.dump(keep, f, ensure_ascii=False)
            os.replace(path + ".tmp", path)
        except OSError:
            pass
    return out


def paths_here(machine):
    """([path], {path: plugin}, [problem]): the plugin.json of every plugin "on" this machine that has schedules,
    and the schedule files they name. A plugin whose place is unknown is left out, and said."""
    rows, problems = plugin.on_this_machine(machine)
    problems = list(problems)  # ours to add to
    try:
        settings = config.settings()
    except Exception as e:  # on_this_machine has said it already, unless it read them before they broke
        settings = {}
        if not any(p.startswith("實例設定讀不出來") for p in problems):
            problems.append("實例設定讀不出來（%s）：排程檔的設定拿不到" % e)
    paths, labels = [], {}
    for name, folder, d, where, reason in rows:
        if where == "unknown":
            problems.append("%s 在不在這台跑不知道（%s），這輪不看它的排程" % (name, reason))
            continue
        if where != "on" or d is None or name == "core":
            continue
        try:  # one plugin's trouble is said for that plugin; the others still count
            if d.get("schedules"):
                path = os.path.join(folder, "plugin.json")
                paths.append(path)
                labels[path] = name
            for path in plugin.schedule_files(d, folder, settings):  # the one reading, apply's too
                paths.append(path)
                labels[path] = name
        except Exception as e:
            problems.append("%s 的排程檔：%s" % (name, e))
    return paths, labels, problems


SAMPLE_LIMIT = 20  # seconds: a sampling that takes longer (a trace on a file system that hangs) is left behind
GAP = 150  # seconds: samples further apart than this are two stretches; what fell between was not looked at
HISTORY_MAX = 512 * 1024  # bytes: trace-history is renamed to .1 beyond this (about a week each on a busy machine)


def history_paths(machine):
    """(history, seen, pid) beside the scheduler's state: trace-history.<machine>.jsonl has a line for every new
    time a trace showed ({"trace", "t", "at"}) and one for every stretch of rounds that sampled ({"span": [from,
    to]}, written when it ends); trace-seen.<machine>.json the last times and the stretch still going on."""
    import re
    name = re.sub(r"[/\\\0]", "_", machine)
    d = scheduler.state_dir()
    return (os.path.join(d, "trace-history.%s.jsonl" % name), os.path.join(d, "trace-seen.%s.json" % name),
            os.path.join(d, "trace-sample.%s.pid" % name))


def trace_id(file, key):
    return json.dumps([file, key], ensure_ascii=False)


def traces_here(paths):
    """The traces the schedules in `paths` name: [(file, key)], each once. One written wrong is left out here
    (--compare says what is wrong with it)."""
    found = set()
    for _path, sched in scheduler._load_all(paths, None)[0]:
        for item in sched.get("items", []):
            raw = item["do"].get("trace")
            if raw is None:
                continue
            try:
                file, key, _within = trace_of(raw)
            except ValueError:
                continue
            found.add((file, key))
    return sorted(found, key=lambda t: (t[0], t[1] or ""))


def sample(machine, traces, now):
    """One sampling (in a child of the round, see sample_round): the time each trace shows now, a history line
    for each that changed, and the stretch of sampling rounds carried on or a new one begun. Only reads the
    traces (a stat, or the JSON file under its key); never writes there. Returns problems to say."""
    history, seen_path, _pid = history_paths(machine)
    seen = config.read_json(seen_path)
    seen = seen if isinstance(seen, dict) else {}
    last = seen.get("last") if isinstance(seen.get("last"), dict) else {}
    span = seen.get("span")
    lines, problems = [], []
    if not (isinstance(span, list) and len(span) == 2 and all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in span)):
        span = None
    if span is not None and not (0 <= now - span[1] <= GAP):  # a gap (or a clock gone back): that stretch is over
        lines.append({"span": span})
        span = None
    span = [span[0], now] if span else [now, now]
    for file, key in traces:
        try:
            t = trace_time(file, key)
        except ValueError:
            continue  # not there now: nothing shows; --compare reads it itself and says why
        tid = trace_id(file, key)
        if last.get(tid) != t:
            last[tid] = t
            lines.append({"trace": tid, "t": t, "at": now})
    try:
        os.makedirs(os.path.dirname(history), exist_ok=True)
        if lines:
            try:
                if os.path.getsize(history) > HISTORY_MAX:
                    os.replace(history, history + ".1")
            except OSError:
                pass
            with open(history, "a", encoding="utf-8") as f:
                f.write("".join(json.dumps(l, ensure_ascii=False, sort_keys=True) + "\n" for l in lines))
        tmp = "%s.%d.tmp" % (seen_path, os.getpid())
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"last": last, "span": span}, f, ensure_ascii=False)
        os.replace(tmp, seen_path)
    except OSError as e:
        problems.append("痕跡的歷史寫不進去：%s" % e)
    return problems


def still_sampling(pid_path, words=("cron_schedule.py", "--sample")):
    """The pid in pid_path when it is a sampling of ours still there (and the file is under 6 hours old), else 0.
    Empty, 0 or below, a process gone, or a live process that is not `cron_schedule.py --sample` (the number
    reused after a restart): none (#code-review 2026-10-08). `words`: what its command line holds (the apply
    report's child is `plugins.py report`)."""
    import subprocess
    try:
        with open(pid_path) as f:
            pid = int(f.read().strip() or 0)
        if pid <= 0 or time.time() - os.path.getmtime(pid_path) >= 6 * 3600:
            return 0
        os.kill(pid, 0)
    except (OSError, ValueError):
        return 0
    try:
        what = subprocess.run(["ps", "-o", "command=", "-p", str(pid)], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                              timeout=5).stdout.decode("utf-8", "replace")
    except (OSError, subprocess.SubprocessError):
        return pid  # cannot be told: taken as ours, as before
    return pid if all(w in what for w in words) else 0


def sample_round(machine, paths, now):
    """After the round is done and marked: sample the traces in a child process, waited for SAMPLE_LIMIT seconds
    at most, so a trace on a file system that hangs never holds the round (the child is left behind, and the next
    rounds do not start another while it is there). Returns problems to say."""
    import subprocess
    traces = traces_here(paths)
    if not traces:
        return []
    _history, _seen, pid_path = history_paths(machine)
    old = still_sampling(pid_path)
    if old:
        return ["上一次的取樣還沒結束（pid %d），這一輪沒有取樣" % old]
    try:
        child = subprocess.Popen([sys.executable, os.path.abspath(__file__), "--sample"], stdin=subprocess.PIPE,
                                 stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    except OSError as e:
        return ["取樣起不來：%s" % e]
    try:
        os.makedirs(os.path.dirname(pid_path), exist_ok=True)
        with open(pid_path, "w") as f:
            f.write("%d\n" % child.pid)
    except OSError:
        pass
    try:
        out, _ = child.communicate(json.dumps({"machine": machine, "traces": traces, "now": now}).encode(), timeout=SAMPLE_LIMIT)
    except subprocess.TimeoutExpired:
        child.kill()
        return ["取樣超過 %d 秒，這一輪沒有取樣完（痕跡檔所在的檔案系統可能卡住）" % SAMPLE_LIMIT]
    except OSError as e:
        return ["取樣：%s" % e]
    try:
        os.remove(pid_path)
    except OSError:
        pass
    said = [" ".join(l.split()) for l in out.decode("utf-8", "replace").splitlines() if l.strip()]
    return ["取樣：%s" % l[:300] for l in said[:5]] + ([] if child.returncode == 0 else ["取樣結束碼 %d" % child.returncode])


REPORT_EVERY = 900  # seconds: how often a machine works out what apply would change (3a.12), at its own minute
REPORT_LIMIT = 60   # seconds: a report that takes longer is left behind (and no other started while it is there)
REPORT_HISTORY_MAX = 256 * 1024  # bytes: apply-report.<machine>.jsonl is renamed to .1 beyond this


def report_paths(machine):
    """(report, history, pid): apply-report.<machine>.json is the latest report whole; .jsonl gets a line when what
    a report says differs from the one before; apply-reporting.<machine>.pid is there while one is being worked out."""
    import re
    name = re.sub(r"[/\\\0]", "_", machine)
    d = scheduler.state_dir()
    return (os.path.join(d, "apply-report.%s.json" % name), os.path.join(d, "apply-report.%s.jsonl" % name),
            os.path.join(d, "apply-reporting.%s.pid" % name))


def report_command():
    """The child that works the report out: reads this machine's crontab and settings.json, prints JSON, writes
    nothing (ops/plugins.py report)."""
    return [sys.executable, os.path.join(ENGINE, "ops", "plugins.py"), "report"]


def report_due(machine, now):
    """Is this the round of this machine's quarter hour? (Its own minute, as its other schedules spread.)"""
    return int(now) // 60 % (REPORT_EVERY // 60) == scheduler.machine_offset(REPORT_EVERY, machine) // 60 % (REPORT_EVERY // 60)


def report_round(machine, now):
    """3a.12, report only: after the round is done and marked, every REPORT_EVERY, what `plugins.py apply --write`
    would change on this machine is worked out by `ops/plugins.py report` (which reads, and calls nothing that
    writes) in a child process, waited for REPORT_LIMIT seconds at most, and kept beside the scheduler's state
    for doctor to show. Nothing is ever applied from here: there is no code here that could. core.auto_apply
    "off" in the instance's settings: not even worked out; anything but "off" or "report" is taken for "report"
    and said in the report. Returns problems to say."""
    import subprocess
    try:
        mode = config.setting("core.auto_apply", "report")
    except Exception:
        mode = "report"
    if mode == "off" or not report_due(machine, now):
        return []
    path, history, pid_path = report_paths(machine)
    old = still_sampling(pid_path, ("plugins.py", "report"))
    if old:
        return ["上一次的套用報告還沒算完（pid %d），這一輪沒有算" % old]
    try:
        child = subprocess.Popen(report_command(), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except OSError as e:
        return ["套用報告起不來：%s" % e]
    try:
        os.makedirs(os.path.dirname(pid_path), exist_ok=True)
        with open(pid_path, "w") as f:
            f.write("%d\n" % child.pid)
    except OSError:
        pass
    try:
        out, err = child.communicate(timeout=REPORT_LIMIT)
    except subprocess.TimeoutExpired:
        child.kill()
        return ["套用報告超過 %d 秒沒有算完，這一輪沒有報告" % REPORT_LIMIT]
    except OSError as e:
        return ["套用報告：%s" % e]
    try:
        os.remove(pid_path)
    except OSError:
        pass
    try:
        report = json.loads(out.decode("utf-8", "replace"))
        if child.returncode != 0 or not isinstance(report, dict) or report.get("format") != 1:
            raise ValueError("結束碼 %d" % child.returncode)
    except ValueError as e:
        said = " ".join(err.decode("utf-8", "replace").split())[-200:]
        report = {"format": 1, "machine": machine, "failed": "算不出來（%s）%s" % (e, "：" + said if said else "")}
    report["at"] = int(now)
    if mode not in ("report", "off"):
        report["note"] = "core.auto_apply 是 %r：這一版只會報告，不會寫" % (mode,)
    same = dict(report, at=0, engine=None, instance=None)  # what it says, apart from when and of which commits
    try:
        with open(path, encoding="utf-8") as f:
            before = json.load(f)
        changed = dict(before, at=0, engine=None, instance=None) != same
    except (OSError, ValueError, TypeError):
        changed = True
    problems = []
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = "%s.%d.tmp" % (path, os.getpid())  # clean_tmp() takes what a killed round leaves
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(json.dumps(report, ensure_ascii=False, indent=1) + "\n")
        os.replace(tmp, path)
        if changed:
            try:
                if os.path.getsize(history) > REPORT_HISTORY_MAX:
                    os.replace(history, history + ".1")
            except OSError:
                pass
            with open(history, "a", encoding="utf-8") as f:
                f.write(json.dumps(report, ensure_ascii=False) + "\n")
    except OSError as e:
        problems.append("套用報告寫不下來：%s" % e)
    return problems


def sample_main():
    """--sample: the child of sample_round. Its problems are its output."""
    if load():
        print("載不進引擎的模組：%s" % BROKEN)
        return 1
    try:
        job = json.loads(sys.stdin.read())
        for p in sample(job["machine"], [tuple(t) for t in job["traces"]], job["now"]):
            print(p)
    except Exception as e:
        print("取樣出錯：%s: %s" % (type(e).__name__, e))
        return 1
    return 0


WITHIN = 300  # seconds: how long after its scheduled time a run may bring its trace up to date, unless the trace says


def trace_of(raw):
    """(file, key, within) of a schedule item's "trace" (scheduler: a path, or {"file", "key", "within"}).
    ValueError with why when it is not one."""
    if isinstance(raw, str) and raw.strip():
        return os.path.expanduser(raw), None, WITHIN
    if not isinstance(raw, dict) or not isinstance(raw.get("file"), str) or not raw["file"].strip():
        raise ValueError("trace 要是檔案路徑，或有 file 的物件，不是 %s" % json.dumps(raw, ensure_ascii=False))
    key = raw.get("key")
    if key is not None and (not isinstance(key, str) or not key.strip()):
        raise ValueError("trace 的 key 要是欄位名稱")
    within = raw.get("within", WITHIN)
    if isinstance(within, str):
        try:
            within = scheduler.duration(within, "smh")
        except scheduler.ScheduleError as e:
            raise ValueError("trace 的 within：%s" % e)
    if isinstance(within, bool) or not isinstance(within, (int, float)) or within <= 0:
        raise ValueError("trace 的 within 要是秒數或像 10m 的長度")
    return os.path.expanduser(raw["file"]), key, within


def trace_time(path, key):
    """When the trace was last brought up to date: the file's modification time, or the time under `key` (a.b for
    one inside another) of the JSON file. ValueError with why when it cannot be had."""
    import datetime
    if key is None:
        try:
            return os.path.getmtime(path)
        except OSError as e:
            raise ValueError("痕跡檔讀不到（%s）" % e)
    try:
        with open(path, encoding="utf-8") as f:
            value = json.load(f)
    except (OSError, ValueError) as e:
        raise ValueError("痕跡檔讀不到（%s）" % e)
    for part in key.split("."):
        value = value.get(part) if isinstance(value, dict) else None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    try:
        return datetime.datetime.fromisoformat(value).timestamp()  # no zone written: this machine's
    except (TypeError, ValueError):
        raise ValueError("痕跡檔的 %s 不是時間（%s）" % (key, json.dumps(value, ensure_ascii=False)[:40]))


def judge(mine, ours, others, t, within, now):
    """(verdict, why) for one item: `mine` its scheduled times in the window (oldest first), `ours` those of every
    item with the same trace, `others` those of items whose output goes to that file (they may bring it up to
    date too), `t` when the trace was last brought up to date. 相符: it was, within `within` after the item's
    last time. 時間不同: it was, at a time that is no scheduled time (the schedule and what really runs differ,
    or a run by hand). 不相符: the last time left no trace. 無法判斷: what the trace shows is not about this item."""
    verdict, why = _judge(mine, ours, others, t, within)
    if verdict == "相符" or now - mine[-1] >= within:
        return verdict, why
    # the last one may still be running: the one before it is what can be checked
    if len(mine) == 1:
        return "無法判斷", "最後一次 %s 才剛到，可能還在跑" % _hm(mine[-1])
    verdict, why = _judge(mine[:-1], [x for x in ours if now - x >= within], others, t, within)
    return verdict, why + "（%s 那次可能還在跑，看前一次）" % _hm(mine[-1])


def _hm(x):
    return time.strftime("%m-%d %H:%M", time.localtime(x))


def _judge(mine, ours, others, t, within):
    last = mine[-1]
    match = max((x for x in ours if x <= t <= x + within), default=None)
    if match == last:
        return "相符", "會跑 %s，痕跡 %s" % (_hm(last), _hm(t))
    if match is not None and match > last:
        return "無法判斷", "痕跡 %s 是同一個痕跡檔裡較晚的另一項（%s）留下的，看不出 %s 那次" % (_hm(t), _hm(match), _hm(last))
    if match is None and t > last and any(x <= t <= x + within for x in others):
        return "無法判斷", "痕跡 %s 可能是輸出寫到同一個檔的其他項目留下的，看不出 %s 那次" % (_hm(t), _hm(last))
    if t > last + within:
        return "時間不同", "會跑 %s，痕跡 %s，不對應任何一次會跑" % (_hm(last), _hm(t))
    return "不相符", "會跑 %s 沒有痕跡（痕跡最後是 %s%s）" % (_hm(last), _hm(t), "，對得上 %s 那次" % _hm(match) if match else "")


VERDICTS = ("相符", "時間不同", "不相符", "無法判斷")


def read_history(machine):
    """What the trace history still holds: {"updates": {trace id: [times it showed]}, "spans": [(from, to)]
    stretches that were sampled, "first": the oldest time a line still kept was written (None: no history),
    "starts": {trace id: from when that trace is known to have been sampled}, "initial": {trace id: the time
    its first line kept showed}}, from trace-history (and its .1)
    and trace-seen. Nothing that cannot be read is taken for anything. What the history no longer holds
    (renamed away) was not sampled: every stretch starts no earlier than `first` (#code-review-2 2026-10-08). A
    trace is sampled from its own first line still kept; one trace-seen knows and no line holds (unchanged since
    before the oldest line) from `first` (#code-review 2026-10-08: a trace written later is not watched before)."""
    history, seen_path, _pid = history_paths(machine)
    updates, spans, first, starts, initial = {}, [], None, {}, {}
    for name in (history + ".1", history):
        try:
            with open(name, encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
        except OSError:
            continue
        for line in lines:
            try:
                e = json.loads(line)
            except ValueError:
                continue
            if not isinstance(e, dict):
                continue
            if isinstance(e.get("trace"), str) and _number(e.get("t")) and _number(e.get("at")):
                updates.setdefault(e["trace"], []).append(e["t"])
                if e["trace"] not in starts:
                    starts[e["trace"]], initial[e["trace"]] = e["at"], e["t"]
                first = e["at"] if first is None else first
            elif _span(e.get("span")):
                spans.append(tuple(e["span"]))
                first = e["span"][1] if first is None else first  # what came in it was written before this line
    seen = config.read_json(seen_path)
    seen = seen if isinstance(seen, dict) else {}
    if _span(seen.get("span")):
        spans.append(tuple(seen["span"]))
    if first is None:
        return {"updates": updates, "spans": [], "first": None, "starts": {}, "initial": {}}
    for tid in seen.get("last") if isinstance(seen.get("last"), dict) else {}:
        starts.setdefault(tid, first)
    return {"updates": updates, "spans": [(max(a, first), b) for a, b in spans if b >= first], "first": first,
            "starts": starts, "initial": initial, "rotated": os.path.exists(history + ".1")}


def _number(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def _span(x):
    return isinstance(x, list) and len(x) == 2 and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in x)


def watched(hist, tid, x, within):
    """None when the history watched trace `tid` all through [x, x + within], else why it did not: the one place
    that says whether a time can be judged (manager 2026-10-08: where nothing is kept, nothing is concluded)."""
    if hist["first"] is None:
        return "還沒有痕跡的歷史"
    if x < hist["first"]:
        return "早於留下來的歷史（歷史已輪替）" if hist.get("rotated") else "早於痕跡的歷史開始"
    start = hist["starts"].get(tid)
    if start is None or x < start:
        return "那時的 trace 沒被取樣"
    if not any(a <= x and b >= x + within for a, b in hist["spans"]):
        return "那段沒有取樣"
    return None


def _label(how):
    if how is None:
        return "沒有 trace"
    if isinstance(how, ValueError):
        return "寫錯的 trace"
    return "%s%s" % (how[0].replace(os.path.expanduser("~"), "~", 1), " 的 %s" % how[1] if how[1] else "")


def judge_history(times, hist, ours, others, now, start, recorded=None):
    """(verdict, why) for one item: `times` [(scheduled time, how)] in the window, oldest first, each with the
    trace its own would-run line named (how: trace_of(), None when it named none, a ValueError when it named one
    written wrong). Each time is judged against the history of its own trace: an update within `within` after it
    is a hit; watched() through and none is 不相符; not watched is 無法判斷 with why. 時間不同: an update of one
    of these traces in the window at no scheduled time of `ours` ({trace id: times of every item whose line named
    it}), of this item whatever its line named then (a run goes on writing a trace while the line names another),
    or of `others` ({file: times of items whose output goes there}); updates before the would-run log's oldest
    line (`recorded`) are left out. Times not yet within + GAP old are left for later."""
    hit, miss, why_not, waiting, used = [], [], {}, 0, {}
    for x, how in times:
        if not isinstance(how, tuple):
            why_not.setdefault("那時沒有 trace" if how is None else "那時的 trace 寫錯（%s）" % how, []).append(x)
            continue
        file, key, within = how
        if now - x < within + GAP:
            waiting += 1
            continue
        tid = trace_id(file, key)
        used[tid] = how
        if any(x <= u <= x + within for u in hist["updates"].get(tid, [])):
            hit.append(x)
            continue
        reason = watched(hist, tid, x, within)
        if reason == "那時的 trace 沒被取樣" and tid not in hist["starts"]:
            try:
                trace_time(file, key)
            except ValueError as e:
                reason = str(e)  # never once in the history and not readable now: a wrong path, not a miss
        if reason:
            why_not.setdefault(reason, []).append(x)
        else:
            miss.append(x)
    extra = []
    for tid, (file, key, within) in used.items():
        since = start if recorded is None else max(start, recorded + within)
        near = ours.get(tid, []) + [x for x, _how in times] + ([] if key else others.get(file, []))
        extra += [u for u in hist["updates"].get(tid, []) if since <= u <= now and not any(x <= u <= x + within for x in near)
                  and u != hist.get("initial", {}).get(tid)]  # what it showed when first looked at: no run seen then
    extra.sort()
    more = "（另有 %d 次剛到，還不算）" % waiting if waiting else ""
    judged = len(hit) + len(miss)

    def at(xs):
        return "、".join(_hm(x) for x in xs[-3:]) + ("…" if len(xs) > 3 else "")
    if miss:
        return "不相符", "%d 次裡有 %d 次沒有更新：%s%s" % (judged, len(miss), at(miss), more)
    if extra:
        return "時間不同", "更新在 %s，不對應任何一次會跑（最後一次會跑 %s）%s" % (at(extra), _hm(times[-1][0]), more)
    if why_not:
        parts = "、".join("%d 次%s（%s）" % (len(xs), r, at(xs)) for r, xs in why_not.items())
        return "無法判斷", "%s，%d 次對得到%s" % (parts, len(hit), more)
    if not hit:
        return "無法判斷", "最後一次 %s 才剛到，可能還在跑" % _hm(times[-1][0])
    return "相符", "%d 次都對得到更新%s" % (len(hit), more)


def switched(times):
    """「視窗內 trace 換過：前 3 次用 X、後 5 次用 Y」, or "" when every time named the same trace."""
    runs = []
    for _x, how in times:
        name = _label(how)
        if runs and runs[-1][0] == name:
            runs[-1][1] += 1
        else:
            runs.append([name, 1])
    if len(runs) < 2:
        return ""
    words = ["前"] + ["接著"] * (len(runs) - 2) + ["後"]
    return "；視窗內 trace 換過：" + "、".join("%s %d 次用 %s" % (w, n, name) for w, (name, n) in zip(words, runs))


def compare(hours, out=sys.stdout, now=None):
    """--compare: for each item the record-only mode said would run in the last `hours` hours, whether it really
    ran then, judged from the trace its would-run line named (scheduler: "trace", a file every run brings up to
    date): 相符／時間不同／不相符／無法判斷 with why, and a count at the end saying from when the would-run log
    and the trace history cover. Every scheduled time is checked against the history of its own trace (sampled
    by the minute rounds); before there is any, only the last one against the trace as it is now. A log is not
    taken for a trace: it is written only when there is output. Read only."""
    import datetime
    now = time.time() if now is None else now
    start = now - hours * 3600
    machine = plugin.this_machine([])
    path = scheduler.log_path(machine)
    seen, recorded = {}, None
    for name in (path + ".1", path):
        try:
            with open(name, encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
        except OSError:
            continue
        for line in lines:
            try:
                e = json.loads(line)
            except ValueError:
                continue
            if not isinstance(e, dict) or "item" not in e:
                continue
            try:
                slot = datetime.datetime.fromisoformat(e.get("slot", "")).timestamp()
                written = datetime.datetime.fromisoformat(e.get("at", "")).timestamp()
            except (TypeError, ValueError):
                continue
            recorded = written if recorded is None else min(recorded, written)  # when the oldest line kept was written
            try:
                how = trace_of(e["trace"]) if e.get("trace") is not None else None
            except ValueError as err:
                how = err
            got = seen.setdefault((e.get("file", "?"), e["item"]), {"times": [], "all": [], "log": None})
            got["all"].append((slot, how))  # those before the window too: an update just inside it may be theirs
            if slot >= start:
                got["times"].append((slot, how))
            got["log"] = e.get("log") or got["log"]
    seen = {k: g for k, g in seen.items() if g["times"]}
    if not seen:
        out.write("最近 %d 小時沒有任何「會跑」的紀錄（%s）\n" % (hours, path))
        return 0
    ours, others = {}, {}
    for got in seen.values():
        got["times"].sort(key=lambda t: t[0])
        for x, how in got["all"]:
            if isinstance(how, tuple):
                ours.setdefault(trace_id(how[0], how[1]), []).append(x)
            if got["log"] and not (isinstance(how, tuple) and how[1] is None and how[0] == os.path.expanduser(got["log"])):
                others.setdefault(os.path.expanduser(got["log"]), []).append(x)  # its output may bring the file up to date
    hist = read_history(machine)
    counts = dict.fromkeys(VERDICTS, 0)
    for (f, item), got in sorted(seen.items()):
        times = got["times"]
        how = times[-1][1]  # what the item names now
        if hist["first"] is not None:
            verdict, why = judge_history(times, hist, ours, others, now, start, recorded)
        elif how is None:
            verdict, why = "無法判斷", ("沒有 trace；log 只在有輸出時才寫，不能當證據" if got["log"] else "沒有 trace")
        elif isinstance(how, ValueError):
            verdict, why = "無法判斷", str(how)
        else:
            file, key, within = how
            mine = [x for x, h in times if h == how]
            try:
                verdict, why = judge(mine, sorted(x for x in ours.get(trace_id(file, key), []) if x >= start),
                                     sorted(x for x in ([] if key else others.get(file, [])) if x >= start),
                                     trace_time(file, key), within, now)
                why += "（還沒有取樣的歷史，只比最後一次）"
            except ValueError as e:
                verdict, why = "無法判斷", str(e)
        if how is None and hist["first"] is not None and all(h is None for _x, h in times):
            why = ("沒有 trace；log 只在有輸出時才寫，不能當證據" if got["log"] else "沒有 trace")
        elif isinstance(how, tuple):
            why += "；%s" % _label(how)
        why += switched(times)
        counts[verdict] += 1
        out.write("[%s] %s/%s：會跑 %d 次，%s\n" % (verdict, os.path.basename(os.path.dirname(f)), item, len(times), why))
    cover = "；會跑的紀錄涵蓋 %s 起" % _hm(recorded) + \
        ("；痕跡的歷史涵蓋 %s 起" % _hm(hist["first"]) if hist["first"] is not None else "；還沒有痕跡的歷史")
    out.write("最近 %d 小時共 %d 項：%s%s\n" % (hours, len(seen), "／".join("%s %d" % (v, counts[v]) for v in VERDICTS), cover))
    return 0


def main(argv=()):
    """One round, its stdout and stderr taken over (file descriptors 1 and 2 too, so a child's or a module's
    writing is caught): what turns up there is said once an hour, never left for cron to append every minute."""
    if argv[:1] == ["--sample"]:  # the child of a round's sampling: its output goes back to the round
        return sample_main()
    if argv[:1] == ["--compare"]:  # a person's command: prints
        return 1 if load() and not print(BROKEN) else compare(int(argv[1]) if len(argv) > 1 and argv[1].isdigit() else 24)
    import tempfile
    try:
        sink = tempfile.TemporaryFile()
        sys.stdout.flush()
        sys.stderr.flush()
        saved = (os.dup(1), os.dup(2), sys.stdout, sys.stderr)
    except (OSError, ValueError):
        load()
        return _main(argv)  # nothing to take them over with: as it is
    os.dup2(sink.fileno(), 1)
    os.dup2(sink.fileno(), 2)
    sys.stdout = sys.stderr = open(1, "w", encoding="utf-8", errors="replace", closefd=False)
    try:
        load()
        return _main(argv)
    finally:
        try:
            sys.stdout.flush()
        except (OSError, ValueError):
            pass
        os.dup2(saved[0], 1)
        os.dup2(saved[1], 2)
        os.close(saved[0])
        os.close(saved[1])
        sys.stdout, sys.stderr = saved[2], saved[3]
        sink.seek(0)
        lines = [" ".join(l.split()) for l in sink.read().decode("utf-8", "replace").splitlines() if l.strip()]
        sink.close()
        if lines and not REFUSED:
            said = to_say("output", sorted(set(lines))[:20], time.time()) if not BROKEN else lines[:1]
            for p in said:
                say("輸出：%s" % p[:300])


def _main(argv):
    if BROKEN:
        say("載不進引擎的模組，沒有記：%s" % BROKEN[:300])  # bounded by say()'s renaming
        return 0
    try:
        global REFUSED
        kind, _where, why = engine_id.check(ENGINE)
        if kind == "other":  # another checkout: nothing written at all (#code-review-2, manager 2026-10-07)
            REFUSED = True
            return 0
        if kind != "official":  # cannot be told: nothing recorded, said (bounded) so someone can see why
            for p in to_say("refused", ["沒有記：%s" % why], time.time()):
                say(p)
            return 0
        clean_tmp()
        return one_round()
    except Exception as e:  # anything left: one bounded line, never a traceback every minute
        import traceback
        where = traceback.extract_tb(e.__traceback__)[-1]
        for p in to_say("crashed", ["這一輪出錯，沒有記完：%s: %s（%s:%d）" % (type(e).__name__, e, os.path.basename(where.filename),
                                                                    where.lineno)], time.time()):
            say(p)
        return 0


def clean_tmp(older=3600):
    """The <file>.<pid>.tmp a round killed halfway leaves beside the scheduler's state: gone after an hour."""
    try:
        names = os.listdir(scheduler.state_dir())
    except OSError:
        return
    for n in names:
        p = os.path.join(scheduler.state_dir(), n)
        try:
            if n.endswith(".tmp") and time.time() - os.path.getmtime(p) > older:
                os.remove(p)
        except OSError:
            pass


def one_round():
    problems = []
    machine = plugin.this_machine(problems)
    paths, labels, found = paths_here(machine)
    problems += found
    tz = scheduler.timezone()[0]  # not known: the round itself says so (core.timezone) and holds what needs it
    due, round_problems = scheduler.record_only(paths, machine, tz=tz, labels=labels)
    if due:
        say("會跑（只記錄，沒有執行）：%s" % "、".join("%s/%s" % (labels.get(d["path"], "?"), d["item"]["name"]) for d in due))
    problems += ["%s：%s" % (what, text) for what, text in round_problems]
    for p in to_say(machine, problems, time.time()):  # the same problem every minute: said once an hour, not 1440 times
        say("問題：%s" % p)
    try:  # after the round is done and marked; nothing here can make it not done
        sampled = sample_round(machine, paths, int(time.time()))
    except Exception as e:
        sampled = ["取樣出錯：%s: %s" % (type(e).__name__, e)]
    for p in to_say("sample-" + machine, sampled, time.time()):
        say("痕跡：%s" % p)
    try:  # 3a.12: what apply would change here, worked out and kept; nothing applied
        reported = report_round(machine, int(time.time()))
    except Exception as e:
        reported = ["套用報告出錯：%s: %s" % (type(e).__name__, e)]
    for p in to_say("report-" + machine, reported, time.time()):
        say("套用報告：%s" % p)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
