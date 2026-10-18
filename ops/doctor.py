#!/usr/bin/env python3
"""Check every machine against the machine list and report anything that doesn't match.

  python3 ops/doctor.py                  # all machines
  python3 ops/doctor.py host-a host-b    # some machines
  python3 ops/doctor.py --fix            # the same, with how to fix each problem under it
  python3 ops/doctor.py --json           # the records below, as a JSON list (for programs and the install guide)

Every check makes one record: {"id", "machine", "status", "msg", "fix"} and, for some, "value".
- id: a stable name, `<area>.<check>` (core.global-guide, chatlog.last-sync, dcbot.heartbeat).
- machine: its name in the list; null for a check about all of them together (progress.single, core.privacy-scan).
- status: "ok"; "fail" (a problem: listed, exit status 1); "warn" (shown, not a problem); "off" (turned off on
  purpose; none yet).
- msg: one sentence for people. fix: a command or what to do, "" when ok.
- value: the bare fact behind it (a version, a time), when the table needs one.
What people read (the table, the list of problems) is made from these same records. A check that does not apply to
a machine (that plugin is not placed on it) makes no record. With no machine list there is this machine only: nothing
about versions agreeing or about SSH is checked.
"""
import json
import os
import shlex
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

from common import INSTALL_CRON, START_CRON, engine_raw, inventory, local_head_status, run_on

ENGINE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ENGINE_DIR)
from altention import plugin as altplugin  # noqa: E402

PROBE = r'''
import json, os, shlex, socket, subprocess, sys, fcntl
H = os.path.expanduser
def sh(c):
    try:
        return subprocess.run(c, shell=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                              universal_newlines=True, timeout=60).stdout.strip()
    except Exception:
        return ""
def rj(p):
    try:
        with open(H(p)) as f:
            return json.load(f)
    except Exception:
        return {}
o = {"hostname": socket.gethostname(), "python": sys.version.split()[0]}
ch = H(ENGINE)
if os.path.lexists(ch + "/.git"):  # a clone (or a worktree): its commit; "" when git cannot read it
    o["code"] = sh("git -C %s rev-parse --short HEAD" % shlex.quote(ch))
elif os.path.isfile(os.path.join(ch, "altention", "config.py")):  # unpacked from an archive: no commit to read
    o["code"] = "archive"
else:
    o["code"] = None
cron = sh("crontab -l 2>/dev/null").splitlines()
o["crontab_missing"] = __import__("shutil").which("crontab") is None  # then nothing can write a schedule or a hook
o["uptime"] = None  # seconds since this machine booted, when it can be read (doctor: did it sleep?)
try:
    with open("/proc/uptime") as _f:
        o["uptime"] = float(_f.read().split()[0])
except Exception:
    try:  # macOS: kern.boottime "{ sec = 1760000000, usec = 0 } ..."
        o["uptime"] = __import__("time").time() - int(sh("sysctl -n kern.boottime").split("sec = ")[1].split(",")[0])
    except Exception:
        pass
o["slept"] = None  # seconds this machine has been suspended since it booted (Linux: CLOCK_BOOTTIME counts them, monotonic not)
try:
    o["slept"] = max(0.0, __import__("time").clock_gettime(__import__("time").CLOCK_BOOTTIME) - __import__("time").monotonic())
except Exception:  # macOS and the like: cannot be told
    pass
o["cron_running"] = None  # Linux: a cron daemon seen (common.cron_running, the same way); None: cannot be told
if sys.platform.startswith("linux"):
    for _n in ("cron", "crond"):
        try:
            _rc = subprocess.run(["pgrep", "-x", _n], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10).returncode
        except Exception:
            _rc = None
        if _rc == 0:
            o["cron_running"] = True
            break
        if _rc != 1:
            o["cron_running"] = None
            break
        o["cron_running"] = False
import re as _re
_seen = {}
for _l in cron:  # our job lines, time fields and trailing marker aside: the same command twice runs twice
    if _l.lstrip().startswith("#") or (H(ENGINE) + "/") not in _l.replace("~/", os.path.expanduser("~") + "/"):
        continue
    _p = _l.split(None, 1 if _l.lstrip().startswith("@") else 5)
    _cmd = " ".join(_p[:-1] + [_re.sub(r"\s+#\s*(altention:)?[A-Za-z0-9_-]+(/[A-Za-z0-9_.-]+)?\s*$", "", _p[-1]).strip()]) if _p else ""
    _seen[_cmd] = _seen.get(_cmd, 0) + 1  # the same time and the same command: @reboot and */5 of one command are two
o["cron_dupes"] = sorted(c for c, n in _seen.items() if n > 1)
_named = {}  # our lines by the item their marker names (# altention:<plugin>/<name>): one item, one line
for _l in cron:
    _mk = None if _l.lstrip().startswith("#") else _re.search(r"#\s*altention:([A-Za-z0-9_-]+)/([A-Za-z0-9_.-]+)\s*$", _l)
    if _mk:
        _named[_mk.group(1) + "/" + _mk.group(2)] = _named.get(_mk.group(1) + "/" + _mk.group(2), 0) + 1
o["cron_named_twice"] = sorted(k for k, n in _named.items() if n > 1)
o["cron_chatlog"] = any("chatlog.py" in l and not l.lstrip().startswith("#") for l in cron)
o["cron_progress"] = any("progress/progress.py" in l and not l.lstrip().startswith("#") for l in cron)
o["cron_progress_paused"] = any("progress/progress.py" in l and l.lstrip().startswith("#") for l in cron)
s = rj("~/.claude/settings.json")
hooks = s.get("hooks") or {}
def has(ev):
    return any("chatlog.py" in h.get("command", "") for g in hooks.get(ev, []) for h in g.get("hooks", []))
o["hooks"] = has("Stop") and has("SessionEnd")
o["cleanup_days"] = s.get("cleanupPeriodDays")
c = rj("~/.cache/chatlog/state.json")
o["chatlog_last_sync"], o["chatlog_error"] = c.get("last_sync"), c.get("last_error")
o["chatlog_big"] = []  # this machine's files in the logs repo past 50 MB (GitHub refuses past 100 MB)
try:  # the folder chatlog writes into: its name as chatlog works it out (altention's host file first)
    _name = open(H("~/.config/altention/host")).read().strip()
except Exception:
    _name = ""
o["chatlog_host"] = _name or socket.gethostname().split(".")[0]
_data = os.environ.get("ALT_DATA") or os.path.join(os.environ.get("ALT_ROOT") or "~/altention", "data")  # {data}
_mine = os.path.join(H(os.environ.get("CHATLOG_REPO") or os.path.join(_data, "logs")), o["chatlog_host"])
for _d, _dirs, _files in os.walk(_mine):
    for _f in _files:
        try:
            _n = os.path.getsize(os.path.join(_d, _f))
        except OSError:
            continue
        if _n > 50 * 1024 * 1024:
            o["chatlog_big"].append([os.path.relpath(os.path.join(_d, _f), _mine), _n])
o["chatlog_unsure"] = []  # folders with raw segments that meta.json gives no complete list of: export leaves them alone
_segs = [_d for _d, _dirs, _files in os.walk(_mine) if any(_re.match(r"^.+-\d{4}\.jsonl(\.gz)?", _f) for _f in _files)]
if _segs:
    try:
        sys.path.insert(0, os.path.join(H(ENGINE), "chatlog"))
        import chatlog as _cl
        for _d in sorted(set(os.path.dirname(_d) if os.path.basename(_d) == "subagents" else _d for _d in _segs)):
            if _cl.unsure(_d):
                o["chatlog_unsure"].append(os.path.relpath(_d, _mine))
    except Exception as _e:  # an engine without it (older): not known, not said
        pass
p = rj("~/.cache/progress/state.json")
o["progress_last_run"], o["progress_errors"] = p.get("last_run"), p.get("last_errors")
o["progress_failed"] = p.get("last_failed") if isinstance(p.get("last_failed"), dict) else None
o["progress_log_tail"] = None
try:  # the cron line's output: the last thing a round said, when it stopped before it could note it
    with open(H("~/.cache/progress/cron.out"), "rb") as f:
        f.seek(0, 2)
        f.seek(max(0, f.tell() - 4096))
        _tail = [l for l in f.read().decode("utf-8", "replace").splitlines() if l.strip()]
    o["progress_log_tail"] = _tail[-1].strip()[:300] if _tail else None
except OSError:
    pass
def _mt(p):
    try:
        return os.path.getmtime(H(p))
    except OSError:
        return None
# when each output was last written, and when that plugin's code here last changed (an update): an output older than
# the code (or than its schedule line, cron_added) was written by what ran before and says nothing of what runs now
o["progress_log_mtime"], o["progress_code_mtime"] = _mt("~/.cache/progress/cron.out"), _mt(os.path.join(ENGINE, "progress", "progress.py"))
o["usage_log_mtime"], o["usage_code_mtime"] = _mt("~/.cache/usage.out"), _mt(os.path.join(ENGINE, "usage", "collect.py"))
_u = rj("~/.cache/usage/state.json")  # usage/collect.py's notes
o["usage_last_run"] = _u.get("last_run") if isinstance(_u.get("last_run"), str) else None
o["usage_failed"] = _u.get("last_failed") if isinstance(_u.get("last_failed"), dict) else None
o["usage_errors"] = [str(_e)[:200] for _e in _u.get("last_errors")][:5] if isinstance(_u.get("last_errors"), list) else []
o["usage_log_tail"] = None
try:  # the schedule's output (plugin.json do.log)
    with open(H("~/.cache/usage.out"), "rb") as f:
        f.seek(0, 2)
        f.seek(max(0, f.tell() - 4096))
        _tail = [l for l in f.read().decode("utf-8", "replace").splitlines() if l.strip()]
    o["usage_log_tail"] = _tail[-1].strip()[:300] if _tail else None
except OSError:
    pass
o["progress_running"] = False
lp = H("~/.cache/progress/lock")
if os.path.exists(lp):
    with open(lp, "a") as f:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(f, fcntl.LOCK_UN)
        except OSError:
            o["progress_running"] = True
try:  # a link to the engine's (no instance), or the file the sync composes from the engine's and the instance's
    _g = H("~/.claude/CLAUDE.md")
    if os.path.islink(_g):
        o["global_md_state"] = "link" if os.path.realpath(_g).endswith("/CLAUDE.global.md") else "other link"
    elif not os.path.exists(_g):
        o["global_md_state"] = "missing"
    else:
        o["global_md_state"] = "composed" if open(_g, encoding="utf-8").readline().startswith("<!-- altention：") else "own"
    o["global_md"] = o["global_md_state"] in ("link", "composed")
except OSError:
    o["global_md"], o["global_md_state"] = False, "missing"
o["bot_env"] = os.path.exists(H("~/.config/dcbot/env"))
o["bot_cron"] = any("supervisor.py" in l and not l.lstrip().startswith("#") for l in cron)
o["bot_supervisor"] = False
sl = os.path.join(H(ENGINE), "dcbot/state/supervisor.lock")
if os.path.exists(sl):
    with open(sl, "a") as f:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(f, fcntl.LOCK_UN)
        except OSError:
            o["bot_supervisor"] = True
try:
    import time
    o["bot_heartbeat_age"] = int(time.time()) - int(open(os.path.join(H(ENGINE), "dcbot/state/heartbeat")).read().strip())
except Exception:
    o["bot_heartbeat_age"] = None
try:  # how the bot's last run ended and whether one is going, from the supervisor's own lines (the token check)
    import time
    _p = os.path.join(H(ENGINE), "dcbot/state/supervisor.out")
    with open(_p, "rb") as _f:
        _f.seek(max(0, os.path.getsize(_p) - 65536))
        _ls = _f.read().decode("utf-8", "replace").splitlines()
    _starts = [i for i, l in enumerate(_ls) if " starting bot " in l]
    _exits = [i for i, l in enumerate(_ls) if " bot exited with " in l]
    _ended = _exits[-1] if _exits else -1
    _began = max([i for i in _starts if i < _ended] or [-1])  # the start of the last run that ended
    _why = [l for l in _ls[_began + 1:_ended] if _began >= 0] if _ended >= 0 else []
    o["bot_last_exit"] = ("login" if any(l.startswith("discord.errors.LoginFailure:") for l in _why)  # the exception's own line
                          else "intents" if any(l.startswith("discord.errors.PrivilegedIntentsRequired:") for l in _why)
                          else "other" if _began >= 0 else None)
    if _starts and _starts[-1] > _ended:  # a run going now: since bot.pid was written for it (no time zone to read)
        o["bot_started_age"] = int(time.time() - os.path.getmtime(os.path.join(H(ENGINE), "dcbot/state/bot.pid")))
except Exception:  # no such file yet, or lines this engine does not write: nothing is said from them
    pass
try:  # this machine's instance, and a stray bot setting
    sys.path.insert(0, ch)
    from altention import config as altconfig
    o["instance"] = altconfig.instance_dir()
    if o["instance"]:  # a local instance (install.sh made it) has none: a hint, not a problem
        o["instance_remote"] = subprocess.run(["git", "-C", o["instance"], "remote"], stdout=subprocess.PIPE,
                                              stderr=subprocess.DEVNULL, universal_newlines=True).stdout.strip()
    o["stray_bot"] = altconfig.stray_bot_settings()
    o["this_machine"] = altconfig.this_machine()
except Exception as e:  # an engine too old for altention/, or a broken settings file
    o["config_error"] = "%s: %s" % (type(e).__name__, e)
try:  # the instance's upstream held back by its settings check (sync.instance_held, the one reading of it)
    from altention import sync as altsync
    o["instance_held"] = altsync.instance_held(lambda t: None)
except Exception:  # an engine without it
    pass
try:  # 3a.11: the zone the schedules go by (and where from), and the record-only scheduler's last whole round
    from altention import scheduler as altsched
    _z, _src, _why = altsched.timezone()
    o["timezone"] = {"name": getattr(_z, "name", None), "source": _src, "problem": _why}
    _future = []
    o["schedule_record"] = {"line": any("cron_schedule.py" in l and not l.lstrip().startswith("#") for l in cron),
                            "round": altsched.last_good_round(o.get("this_machine") or altconfig.this_machine(), dry=True,
                                                              future=_future)}
    o["schedule_record"]["future"] = max(_future) if _future else None  # marks not believed: shown, never taken
    try:  # 3a.12: what apply would change on this machine, as its own minute round last worked it out
        with open(os.path.join(altsched.state_dir(), "apply-report.%s.json" % _re.sub(r"[/\\\0]", "_", o.get("this_machine") or altconfig.this_machine())),
                  encoding="utf-8") as _f:
            o["apply_report"] = json.load(_f)
    except (OSError, ValueError):
        o["apply_report"] = {}  # none yet, or not readable: said as "no report", never as "nothing to change"
except Exception:  # an engine without them, or no name for this machine
    pass
o["cron_logs"] = {}  # 3a.10: the last line each core cron line wrote (a skip's reason shows here)
for _n in ("update", "sync", "watch"):
    try:
        with open(H("~/.local/state/altention/cron-%s.log" % _n), encoding="utf-8", errors="replace") as _f:
            _last = [l.strip() for l in _f.readlines()[-20:] if l.strip()]
        o["cron_logs"][_n] = _last[-1] if _last else ""
    except OSError:
        pass
_uo = rj("~/.local/state/altention/last_ok-update")  # update.py on an engine unpacked from an archive: never updated
o["engine_unpacked"] = isinstance(_uo, dict) and _uo.get("checkout") is False
_av = rj("~/.local/state/altention/update-available")  # a newer release notify mode saw (altention/update.py)
o["update_available"] = _av if isinstance(_av, dict) and isinstance(_av.get("tag"), str) else None
_iv = rj("~/.local/state/altention/intervals.json")  # C-37 C: what update.py and the sync count three rounds by
o["intervals"] = _iv if isinstance(_iv, dict) else None
_dp = rj("~/.local/state/altention/deps-pending")  # C-08: HEAD moved, the bot's packages not installed (update.py)
o["deps_pending"] = _dp if isinstance(_dp, dict) else None
_skip = rj("~/.local/state/altention/last_skip-update")
o["update_skipped"] = _skip if isinstance(_skip, dict) else None
_alive = rj("~/.local/state/altention/last_alive-update")
o["update_blocked"] = _alive.get("blocked") if isinstance(_alive, dict) else None
o["update_alive_at"] = _alive.get("at") if isinstance(_alive, dict) else None  # every round that came to its end
o["cron_added"] = {}  # per machine name: when apply last added a cron line of each plugin there (applycore.note_added)
try:
    for _m in os.listdir(H("~/.local/state/altention/apply")):
        _v = rj("~/.local/state/altention/apply/%s/added.json" % _m)
        if isinstance(_v, dict) and _v:
            o["cron_added"][_m] = _v
except OSError:
    pass
o["last_ok"] = {}  # 3a.10: altention/update.py and the sync each write when they last worked
for _n in ("sync", "update"):
    _v = rj("~/.local/state/altention/last_ok-%s" % _n)
    o["last_ok"][_n] = _v.get("at") if isinstance(_v, dict) else None
try:  # what this machine wrote down to tell (dcnotify may have failed exactly then): the last day's, newest last
    with open(H("~/.local/state/altention/alerts"), encoding="utf-8", errors="replace") as _f:
        _all = [l.rstrip("\n").split("\t") for l in _f.readlines()]
    _rows = [r for r in _all if len(r) >= 4]

    def _recent(r):  # the last day's only (manager 2026-10-16): the file stays the record; a time not read counts as recent
        try:
            import datetime as _d
            return __import__("time").time() - _d.datetime.strptime(r[0], "%Y-%m-%dT%H:%M:%S%z").timestamp() <= 86400
        except (ValueError, OverflowError):
            return True
    o["alerts"] = [r for r in _rows[-50:] if _recent(r)][-5:]
    # something pulled in on top of a refused hash while update.py was not checking, and update.py not back since
    # (its first round back writes took-refused): told by --watch (manager 2026-10-07, DESIGN 7.1)
    _took = [i for i, r in enumerate(_rows) if r[2] == "stacked-on-refused"]
    _back = [i for i, r in enumerate(_rows) if r[1] == "update" and r[2] == "took-refused"]
    o["unchecked"] = _rows[_took[-1]][:4] if _took and (not _back or _took[-1] > _back[-1]) else None
    import datetime as _dt
    if o["unchecked"]:
        try:
            o["unchecked"].append(_dt.datetime.strptime(o["unchecked"][0], "%Y-%m-%dT%H:%M:%S%z").timestamp())
        except ValueError:
            o["unchecked"].append(None)
    # a fallback used (a launcher's plain fast-forward; the sync standing in because update.py does not load): the
    # newest of each, with its time, told until that one works again (its last_ok newer)
    o["fallbacks"] = {}
    for r in _rows:
        part = {("cron-update", "fallback"): "update", ("cron-sync", "fallback"): "sync",
                ("sync", "update-broken"): "update"}.get((r[1], r[2]))
        if part:
            try:
                at = _dt.datetime.strptime(r[0], "%Y-%m-%dT%H:%M:%S%z").timestamp()
            except ValueError:
                at = None
            o["fallbacks"][part] = r[:4] + [at]
except OSError:
    o["alerts"], o["unchecked"], o["fallbacks"] = [], None, {}
o["now"] = __import__("time").time()
o["sync"] = rj("~/.local/state/altention/sync.json")  # altention.sync: engine and instance sync, links (stage 2.2)
try:  # /tmp: Claude Code, the bot and the tests all need it; full inodes stop everything (2026-10-06)
    v = os.statvfs("/tmp")
    o["tmp_inodes"] = 100 - int(100 * v.f_favail / v.f_files) if v.f_files else 0
    o["tmp_space"] = 100 - int(100 * v.f_bavail / v.f_blocks) if v.f_blocks else 0
except OSError:
    pass
try:  # C-25: each backup (ops/remote.py status: folder, remote, hook, commits not pushed, last push, last check)
    sys.path.insert(0, os.path.join(ch, "ops"))
    import remote as _remote
    o["backup"] = _remote.status()
    _own = _remote.config.setting("planner.folder")
    for _row in o["backup"]:
        if _row.get("name") == "planner" and isinstance(_own, str) and _own.strip():
            _row["own"] = True  # the owner's own folder (planner.folder): the sync never takes it on (manager 2026-10-16)
        _f = _row.get("folder")
        if isinstance(_f, str) and os.path.isdir(_f):
            _row["mode"] = os.stat(_f).st_mode & 0o777
        if _row.get("origin"):  # the commits on no remote branch, and since when the oldest waits (no upstream too:
            # an attached repo before its first push; a fresh clone has no push on record to go by)
            _t = [int(x) for x in sh("git -C %s log --format=%%ct HEAD --not --remotes" % shlex.quote(_f)).split() if x.isdigit()]
            _row["unpushed"] = len(_t)
            _row["waiting_since"] = min(_t) if _t else None
    _u = H("~/.local/share/usage")  # usage's samples: the fifth private folder
    o["usage_mode"] = os.stat(_u).st_mode & 0o777 if os.path.isdir(_u) else None
except Exception as e:  # an engine without ops/remote.py, or its settings unreadable
    o["backup_error"] = "%s: %s" % (type(e).__name__, e)
o["engine_paths"] = {}
for name in ("knowledge", "memory", "docs/refactor"):  # stage 2.3b: links to the instance once git no longer has them
    if name == "docs/refactor" and not os.path.isdir(os.path.join(o.get("instance") or "", name)):
        continue  # design notes some installs keep in the instance; a new one has none
    path = os.path.join(ch, name)
    tracked = bool(sh("git -C %s ls-files -- %s | head -1" % (shlex.quote(ch), shlex.quote(name))))
    o["engine_paths"][name] = "link" if os.path.islink(path) else ("tracked" if tracked else ("folder" if os.path.isdir(path) else "missing"))
def cron_program(line):
    """The file a cron line really runs, or None: the first .py after an interpreter (python…), else the command's own
    first word; VAR=value words before it are not it (a PROGRESS_CLAUDE= path is no program of the line), a value
    named later as "$VAR" is; a relative path is taken from the folder a leading `cd` went to."""
    try:
        ws = shlex.split(line.split(None, 5)[-1] if not line.lstrip().startswith("@") else line.split(None, 1)[-1], comments=True)
    except ValueError:
        return None
    cmds, cur = [], []
    for w in ws:
        if w in ("&&", "||", ";", "|"):
            cmds.append(cur)
            cur = []
        elif w.endswith(";") and len(w) > 1:
            cur.append(w[:-1])
            cmds.append(cur)
            cur = []
        else:
            cur.append(w)
    cmds.append(cur)
    var, folder = {}, None
    for c in cmds:
        while c and "=" in c[0] and _re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", c[0]):
            k, v = c.pop(0).split("=", 1)
            var[k] = v
        if not c:
            continue
        if c[0] == "cd" and len(c) > 1:
            folder = c[1]
            continue
        if c[0] in ("[", "test", "true", "false", "exec", "flock"):
            continue
        prog = c[0]
        if os.path.basename(prog).startswith("python"):
            prog = next((w for w in c[1:] if w.endswith(".py") or w.startswith("$")), None)
        if not prog:
            return None
        m = _re.match(r"^\$\{?([A-Za-z_][A-Za-z0-9_]*)\}?$", prog)
        if m:
            prog = var.get(m.group(1))
            if not prog:
                return None
        if folder and not prog.startswith(("/", "~")) and ("/" in prog or prog.endswith(".py")):
            prog = os.path.join(folder, prog)  # a bare command name is found on PATH, not in that folder
        return prog
    return None
_re_mark = _re.compile(r"#\s*altention:[A-Za-z0-9_-]+(?:/[A-Za-z0-9_.-]+)?\s*$")
o["cron_dead"] = []  # our lines whose program is not there (an engine moved away): they cannot run, nor run twice
for _l in cron:
    if _l.lstrip().startswith("#") or not _re_mark.search(_l):
        continue
    _prog = cron_program(_l)
    if _prog and (_prog.startswith("/") or _prog.startswith("~/")) and not os.path.exists(H(_prog)):
        o["cron_dead"].append(_l.strip()[:300])
o["entry_links"] = {}  # ~/.local/bin/altention and alt: the command every instruction uses (C-17 S4)
_want = os.path.realpath(os.path.join(ch, "bin", "altention"))
for _n in ("altention", "alt"):
    _p = H("~/.local/bin/" + _n)
    if not os.path.lexists(_p):
        _s = "missing"
    elif not os.path.islink(_p):
        _s = "file"
    elif not os.path.exists(_p):
        _s = "broken"
    elif os.readlink(_p) == _want:
        _s = "ok"
    elif os.path.realpath(_p) == _want:
        _s = "old-name"  # this engine, through a name that is a link now: broken once that name goes
    elif os.path.isfile(os.path.join(os.path.dirname(os.path.dirname(os.path.realpath(_p))), "altention", "config.py")):
        _s = "elsewhere"  # another engine's bin/altention (its folder has altention/config.py), not a program called so
    else:
        _s = "other"
    o["entry_links"][_n] = _s
try:  # copies kept twice (name.<machine>.ext) in the instance: the sync's status forgets them after one run
    import re as _re
    _inst = altconfig.instance_dir()
    _names = sorted(altconfig.machines()) + [altconfig.this_machine() or ""]
    _rx = _re.compile(r"\.(%s)(\.[^./]+)?$" % "|".join(_re.escape(n) for n in _names if n)) if any(_names) else None
    o["kept_twice"] = []
    for _top, _dirs, _files in os.walk(_inst) if _inst and _rx else ():
        _dirs[:] = [d for d in _dirs if d != ".git"]
        o["kept_twice"] += [os.path.relpath(os.path.join(_top, f), _inst) for f in _files if _rx.search(f)]
except Exception:
    pass
try:  # copies of ~/.claude/CLAUDE.md the sync found edited and would not overwrite (2.4e)
    o["global_edited"] = sorted(os.listdir(os.path.join(altconfig.instance_dir(), "global-notes-edited")))
except Exception:
    o["global_edited"] = []
o["adopted"] = [l for l in sh("git -C %s for-each-ref --format='%%(refname)' refs/altention/adopted/" % shlex.quote(ch)).splitlines() if l]
bad = os.path.join(H(ENGINE), "dcbot/state/events-bad")
o["events_bad"] = len([n for n in os.listdir(bad) if n.endswith(".json")]) if os.path.isdir(bad) else 0
o["ownerless"] = rj(os.path.join(H(ENGINE), "dcbot/state/ownerless-sessions.json"))  # the bot: sessions of deleted channels it did not close
_rt = rj(os.path.join(H(ENGINE), "dcbot/state/routes.json"))  # routes written before a route carried its pane's process
o["routes_without_pid"] = sorted(str((v if isinstance(v, str) else (v or {}).get("channel")) if isinstance(v, (str, dict)) else "?")
                                 for v in (_rt.values() if isinstance(_rt, dict) else []) if not isinstance(v, dict) or not v.get("pid"))
_pf = os.path.join(H(ENGINE), "dcbot/state/posts-failed")  # the bot: notifications it could show nowhere
o["posts_failed"] = len([n for n in os.listdir(_pf) if n.endswith(".json") and not n.startswith(".")]) if os.path.isdir(_pf) else 0
o["typed_as_paste"] = rj(os.path.join(H(ENGINE), "dcbot/state/typed-as-paste.json"))  # the bot: typed text taken for a paste
o["enter_differs"] = rj(os.path.join(H(ENGINE), "dcbot/state/enter-differs.json"))  # the bot: Enter on a box that was not what it typed
o["turn_ends_late"] = rj(os.path.join(H(ENGINE), "dcbot/state/turn-ends-late.json"))  # the bot: turns whose end came without its reply's row
o["channels_unreachable"] = rj(os.path.join(H(ENGINE), "dcbot/state/channels-unreachable.json"))  # the bot: channels Discord does not hand out
o["panes_parked"] = rj(os.path.join(H(ENGINE), "dcbot/state/panes-parked.json"))
try:  # the bot: the secretary's channel (planner.channel) works in the secretary's folder? (planner/remind.py --secretary)
    if os.path.exists(os.path.join(H(ENGINE), "dcbot/state/channels.json")):
        from altention import plugin as _plugin
        _pc = altconfig.setting("planner.channel")
        if isinstance(_pc, (str, int)) and not isinstance(_pc, bool) and str(_pc).strip():
            _st = rj(os.path.join(H(ENGINE), "dcbot/state/channels.json")).get(str(_pc).strip()) or {}
            _cwd = _st.get("cwd") if isinstance(_st, dict) and isinstance(_st.get("cwd"), str) else None
            _named = altconfig.setting("planner.folder")  # an owner's own folder for the secretary, when set
            _folder = os.path.realpath(H(_named.strip() if isinstance(_named, str) and _named.strip() else _plugin.expand("{data}/planner")))
            o["planner_secretary"] = {"cwd": os.path.realpath(H(_cwd)) if _cwd else None, "folder": _folder}
except Exception:  # an engine without it, settings that cannot be read: nothing said
    pass  # the bot: panes whose conversation was moved to the background
try:  # what the bot opens a conversation with, read the way the bot reads it (the engine's settings; the connector's list)
    from altention.connectors.claude_code import launch as _launch
    o["open_settings"] = {"valid": list(_launch.permissions())}
    for _k in ("permission", "model"):
        try:
            o["open_settings"][_k] = altconfig.setting("dcbot.default_" + _k)
        except Exception as _e:
            o["open_settings"][_k + "_error"] = "%s: %s" % (type(_e).__name__, _e)
except Exception as _e:
    o["open_settings"] = {"error": "%s: %s" % (type(_e).__name__, _e)}
PRIVATE = [os.path.join(_data, "logs"), os.path.join(_data, "progress"), os.path.join(_data, "logs-mirror"), os.path.join(ENGINE, "dcbot/state"), "~/.cache",
           "~/.local/share/usage"]  # usage's samples (plugin.json private)
o["open_dirs"] = [d for d in PRIVATE if os.path.isdir(H(d)) and os.stat(H(d)).st_mode & 0o077]
try:  # the skills this machine's engine has (install.sh links every folder under skills/), and which are linked
    _skd = os.path.join(H(ENGINE), "skills")
    o["skills_expected"] = sorted(d for d in os.listdir(_skd) if os.path.isdir(os.path.join(_skd, d)))
except OSError:
    o["skills_expected"] = None
o["skills"] = sorted(n for n in (o["skills_expected"] or ()) if os.path.exists(H("~/.claude/skills/" + n)))
print(json.dumps(o))
'''


def age(ts):
    if not ts:
        return None
    try:
        return datetime.now(timezone.utc) - datetime.fromisoformat(ts)
    except ValueError:
        return None


def fmt_age(d):
    if d is None:
        return "—"
    m = int(d.total_seconds() // 60)
    return "%d 分鐘前" % m if m < 120 else "%d 小時前" % (m // 60)


def record(check_id, machine, status, msg, fix="", value=None):
    r = {"id": check_id, "machine": machine, "status": status, "msg": msg, "fix": "" if status == "ok" else fix}
    if value is not None:
        r["value"] = value
    return r


def listed(found):
    """A list the bot keeps in a state file, as the bot writes it: {"at": when, "channels": [ {…}, … ]} (dcbot/bot/
    tmux_bridge.py note_away(), note_parked(); an empty list once there is none any more). -> (the rows, whether
    the file is not what the bot writes). No such file (None, or {} as the probe says it): nothing, and nothing odd."""
    if found is None or found == {}:  # (the probe's word for a file that is not there)
        return [], False
    rows = found.get("channels") if isinstance(found, dict) else None
    if not isinstance(rows, list) or not all(isinstance(r, dict) for r in rows):
        return [], True
    return rows, False


def bot_notes(name, o):
    """The records for what the bot wrote down in its state for somebody to look at, and for the settings it opens
    conversations with (3b 2.6e): `o` is the probe of the bot's machine. Shown, not told, but for the settings."""
    out = []
    if "turn_ends_late" in o:  # shown for a week after the last time, not told
        late = o.get("turn_ends_late") if isinstance(o.get("turn_ends_late"), dict) else {}
        at = _time(late.get("at"))
        days = (o["now"] - at) / 86400.0 if at is not None and o.get("now") else None
        recent = days is not None and -REPORT_AHEAD / 1440.0 <= days < 7
        odd = bool(o.get("turn_ends_late")) and not recent and (at is None or days is None or days < 0)
        out.append(record("dcbot.turn-ends-late", name, "warn" if recent or odd else "ok",
                          ("機器人有 %s 輪的結束等到期限都沒等到那一輪回覆寫進對話紀錄（最近一次 %s，從 %s 算起）：那幾輪的頁尾 context 可能是"
                           "上一輪的或沒有，回覆本身照貼" % (late.get("count", "?"), stamp(at), stamp(_time(late.get("since")))) if recent else
                           "dcbot/state/turn-ends-late.json 讀不懂（不是機器人寫的樣子，或時間在未來）：不知道有沒有結束得比紀錄早的一輪")
                          if recent or odd else "",
                          "看 %s 上 dcbot/state/supervisor.out 裡「bridge: a turn's end was handled without its reply's row」那一行前後；"
                          "常常出現就是 Claude Code 寫紀錄比 hook 慢得多了，調接頭 hooks.py 的 END_HOLD_SECS" % name if recent else
                          "看 %s 上的 dcbot/state/turn-ends-late.json：不是機器人寫的樣子就刪掉它" % name,
                          late.get("count") if recent and isinstance(late.get("count"), int) else None))
    if "channels_unreachable" in o:  # shown, not told: maybe in use, never "may be closed"
        rows, odd = listed(o.get("channels_unreachable"))
        out.append(record("dcbot.channels-unreachable", name, "warn" if rows or odd else "ok",
                          ("Discord 一直拿不到 %d 個頻道，但機器人還留著它們的狀態（可能是 Discord 連不上時正在用的頻道，也可能是機器人不在時被刪的）："
                           "%s。它們的事件沒有地方貼" % (len(rows), "；".join(
                               "%s（從 %s，丟了 %s 個事件）" % (g.get("channel", "?"), stamp(_time(g.get("since"))), g.get("events", "?"))
                               for g in rows[:5])) if rows else
                           "dcbot/state/channels-unreachable.json 讀不懂（不是機器人寫的樣子）") if rows or odd else "",
                          "先看 Discord 上那些頻道還在不在：在的話等連線恢復（或重啟機器人）；真的刪了，它們在 %s 上的對話要不要關由你決定"
                          "（機器人不會自己關）" % name if rows else "看 %s 上的 dcbot/state/channels-unreachable.json" % name, len(rows)))
    if "panes_parked" in o:  # shown, not told: wants the owner's eye at the terminal
        rows, odd = listed(o.get("panes_parked"))
        unanswered = [g for g in rows if g.get("unanswered")]
        out.append(record("dcbot.panes-parked", name, "warn" if rows or odd else "ok",
                          ("有 %d 個頻道的窗格，對話被搬到背景了（終端機按了 ←）：%s。頻道還跟著那個對話，但打進窗格的字要看窗格現在顯示什麼%s"
                           % (len(rows), "；".join("%s（從 %s）" % (g.get("channel", "?"), stamp(_time(g.get("since")))) for g in rows[:5]),
                              "；其中 %d 個最近一則訊息打進去後沒有回音（%s）" % (len(unanswered), "、".join(
                                  str(g.get("channel", "?")) for g in unanswered[:5])) if unanswered else "") if rows else
                           "dcbot/state/panes-parked.json 讀不懂（不是機器人寫的樣子）") if rows or odd else "",
                          "到那些窗格的終端機看：停在背景對話清單就選回原本那一列按 Enter；不想要背景的就在那個頻道 `!reset`（機器人開的視窗）"
                          "或重新 /attach" if rows else "看 %s 上的 dcbot/state/panes-parked.json" % name, len(rows)))
    if "open_settings" in o:
        why = open_settings_problem(o.get("open_settings"))
        out.append(record("dcbot.open-settings", name, "fail" if why else "ok", "機器人會因為這個安裝的設定而不開對話：%s" % why if why else "",
                          "改實例 plugins.json 的那個鍵（或拿掉它，就用預設），在實例 repo commit、push；改好後對頻道再傳一則訊息就會開"))
    return out


def open_settings_problem(found):
    """Will the bot refuse to open conversations because of this installation's settings, and why (a sentence that
    names the key and the value), or None. `found`: what the probe read on the bot's machine, the way the bot
    reads it (altention.config.setting; the connector's launch.permissions()): {"valid", "permission", "model"} or
    "permission_error" / "model_error" / "error". The same two conditions as the bot's own (dcbot/bot/tmux_bridge.py
    with_(); written once there and once here, a test feeds both the same values; one function for both comes with
    step 2.7): the permission mode must be a word of the list, the model a word; a value that is not set is the
    default's, and a model that cannot be read is the default too. A channel that sets its own is not held back
    by the installation's: this says what a channel without its own will meet."""
    if not isinstance(found, dict):
        return None
    if found.get("error"):
        return "讀不到接頭的權限模式清單（%s），doctor 無法判斷" % found["error"]
    if found.get("permission_error"):
        return "讀不到這個安裝的設定（%s）：不知道要用哪個權限模式，沒有自己設權限模式的頻道都不開" % found["permission_error"]
    valid, value, model = found.get("valid") or [], found.get("permission"), found.get("model")
    if value and (not isinstance(value, str) or value not in valid):
        return "plugins.json 的 dcbot.default_permission 是「%s」，不是可以用的值（可以用的：%s）" % (value, "、".join(valid))
    if model and not isinstance(model, str):
        return "plugins.json 的 dcbot.default_model 是「%s」，不是可以用的值（要是一個名稱，例如 opus）" % (model,)
    return None


def check_safely(name, m, head, instance_repo=None):
    """check(), and whatever goes wrong in it for one machine is that machine's one record: the other machines'
    checks, and what doctor --watch tells from them, go on (one state file of another type than expected took
    the whole doctor down: #code-review 2026-10-09, manager)."""
    try:
        return check(name, m, head, instance_repo)
    except Exception as e:
        import traceback
        where = traceback.extract_tb(e.__traceback__)[-1]
        return [record("core.check-error", name, "fail", "檢查出錯：%s: %s（%s:%d）；這台其他的項目這次沒有結果"
                       % (type(e).__name__, str(e)[:160], os.path.basename(where.filename), where.lineno),
                       "多半是那台的某個狀態檔內容不對：看 %s 上 ~/.local/state/altention/ 和 ~/.cache/chatlog/state.json；"
                       "也可能是 doctor 自己的錯，把這一行回報出來" % name)]


def _num(v):
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _time(v):
    """A time read from a file on some machine, when it is one: a finite number of seconds this side of the year
    3000 (1e300, NaN and true are numbers to JSON; turning them into a date raised and took the check, or the
    whole --watch, down: #code-review, #code-review-2 2026-10-09). Else None."""
    v = _num(v)
    return v if v is not None and v == v and 0 < v < 32503680000 else None


def stamp(v, none="時間不明"):
    """"10-09 13:24" for a time from a file, or `none` when it is not a time."""
    v = _time(v)
    try:
        return time.strftime("%m-%d %H:%M", time.localtime(v)) if v is not None else none
    except (OverflowError, ValueError, OSError):
        return none


def tidy(o):
    """The probe's answer with every field of the type check() takes it for; what was of another type is taken
    as not there and named in the list returned with it (one record says so: core.probe-odd). The fields are read
    from files on that machine (state.json, last_ok-*, alerts, sync.json…): a file hand-edited, cut short or
    written by another version must cost that field, not the check. Fields not known here pass as they are."""
    if not isinstance(o, dict):
        raise ValueError("探測回來的不是物件")
    odd = []

    def fix(d, key, good, empty=None, where=""):
        if key in d and d[key] is not None and not good(d[key]):
            odd.append(where + key)
            d[key] = empty() if callable(empty) else empty

    text = lambda v: isinstance(v, str)  # noqa: E731
    texts = lambda v: isinstance(v, list) and all(isinstance(i, str) for i in v)  # noqa: E731
    obj = lambda v: isinstance(v, dict)  # noqa: E731
    num = lambda v: _num(v) is not None  # noqa: E731
    note = lambda v: isinstance(v, list) and len(v) >= 5 and isinstance(v[3], str) and (v[4] is None or num(v[4]))  # noqa: E731
    for key in ("code", "chatlog_last_sync", "chatlog_error", "chatlog_host", "config_error", "instance",
                "instance_remote", "this_machine", "global_md_state", "usage_last_run", "usage_log_tail", "backup_error"):
        fix(o, key, text)
    for key in ("uptime", "slept", "progress_log_mtime", "progress_code_mtime", "usage_log_mtime", "usage_code_mtime"):
        fix(o, key, num)
    fix(o, "usage_mode", num)
    fix(o, "backup", lambda v: isinstance(v, list) and all(isinstance(i, dict) and text(i.get("name")) for i in v), list)
    for row in o.get("backup") or []:
        for key in ("folder", "setting", "origin", "error", "branch"):
            fix(row, key, text, where="backup.")
        fix(row, "remote_branches", texts, list, "backup.")
        for key in ("ahead", "last_push", "mode", "waiting_since", "unpushed"):
            fix(row, key, num, where="backup.")
        fix(row, "check", obj, where="backup.")
        for key in ("repo", "hook", "own"):
            fix(row, key, lambda v: isinstance(v, bool), where="backup.")
    fix(o.get("sync") or {}, "data", obj, dict, "sync.")
    for key in ("now", "cleanup_days", "tmp_inodes", "tmp_space", "events_bad", "posts_failed", "bot_heartbeat_age", "update_alive_at",
                "bot_started_age"):
        fix(o, key, num)
    for key in ("skills", "cron_dupes", "chatlog_unsure", "open_dirs", "kept_twice", "adopted", "global_edited", "routes_without_pid", "usage_errors", "cron_named_twice",
                "cron_dead"):
        fix(o, key, texts, list)
    fix(o, "skills_expected", texts)
    for key in ("sync", "last_ok", "cron_logs", "update_skipped", "update_blocked", "timezone", "schedule_record", "instance_held",
                "fallbacks", "engine_paths", "typed_as_paste", "enter_differs", "ownerless", "cron_added", "usage_failed", "planner_secretary",
                "entry_links"):
        fix(o, key, obj, dict)
    fix(o, "intervals", obj)
    fix(o, "chatlog_big", lambda v: isinstance(v, list) and all(isinstance(i, (list, tuple)) and len(i) == 2 and text(i[0]) and num(i[1])
                                                               for i in v), list)
    fix(o, "alerts", lambda v: isinstance(v, list) and all(isinstance(i, (list, tuple)) and len(i) >= 4 and text(i[3]) for i in v), list)
    fix(o, "unchecked", note)
    fix(o, "update_available", lambda v: isinstance(v, dict) and text(v.get("tag")))
    fix(o, "engine_unpacked", lambda v: isinstance(v, bool), False)
    fix(o, "crontab_missing", lambda v: isinstance(v, bool), False)
    fix(o, "bot_last_exit", lambda v: v in ("login", "intents", "other"))
    fix(o, "cron_running", lambda v: isinstance(v, bool))
    for key, inside in (("last_ok", num), ("cron_logs", text), ("engine_paths", text), ("fallbacks", note), ("entry_links", text)):
        for k in list(o.get(key) or {}):
            fix(o[key], k, inside, where=key + ".")
            if o[key][k] is None and key in ("cron_logs", "engine_paths", "entry_links"):
                del o[key][k]  # read by walking the items: one not there, not one that is None
    for key in ("update_skipped", "update_blocked", "instance_held"):
        fix(o.get(key) or {}, "since", num, where=key + ".")
    fix(o.get("instance_held") or {}, "hash", text, "?", where="instance_held.")
    for key in ("round", "future"):
        fix(o.get("schedule_record") or {}, key, num, where="schedule_record.")
    for key in ("problem", "name", "source"):
        fix(o.get("timezone") or {}, key, text, where="timezone.")
    sy = o.get("sync") or {}
    fix(sy, "links", obj, dict, "sync.")
    fix(sy, "config_error", text, where="sync.")
    fix(sy.get("links") or {}, "twice", texts, list, "sync.links.")
    fix(sy.get("links") or {}, "error", text, where="sync.links.")
    for part in ("engine", "instance"):
        fix(sy, part, obj, dict, "sync.")
        fix(sy.get(part) or {}, "error", text, where="sync.%s." % part)
        fix(sy.get(part) or {}, "rescued", texts, list, "sync.%s." % part)
        fix(sy.get(part) or {}, "ahead", num, where="sync.%s." % part)
    return o, odd


def plugins_on(name):
    """({plugins placed on machine `name`}, {plugins whose place cannot be worked out}), from plugin.json and the
    instance's plugins.json; None when nothing can be worked out (python3 ops/plugins.py show says why). A plugin
    not known is neither on nor off here: none of its checks run and nothing is said of it being somewhere it should
    not (#architecture-review 2026-10-15)."""
    try:
        rows, _ = altplugin.on_this_machine(name)
    except Exception:
        return None
    return {r[0] for r in rows if r[3] == "on"}, {r[0] for r in rows if r[3] == "unknown"}


def unmet_needs(name):
    """[(plugin, needed)]: plugins on machine `name` whose plugin.json "requires" a plugin turned off there on purpose
    ("disabled": its disabled_on names the machine), e.g. progress on and chatlog off. Only what the settings plainly
    say: a placement that cannot be worked out is not taken for off."""
    try:
        rows, _ = altplugin.on_this_machine(name)
    except Exception:
        return []
    where = {r[0]: r[3] for r in rows}
    return [(n, x.get("plugin")) for n, _, d, w, _ in rows if w == "on" and d
            for x in d.get("requires", []) if isinstance(x, dict) and where.get(x.get("plugin")) == "disabled"]


WITHIN_DEFAULT = 300  # seconds a run may take before its record is written, when its plugin.json says no "within"
FIRST_SLACK = 300     # seconds past a new line's first turn (and its within) before "never ran" is a problem


def schedule_of(plugin_name, item_name, machine):
    """{"item", "zone", "within", "off"} for one schedule as it runs on `machine`: plugin.json with the instance's
    override (altplugin.effective_schedules, C-37), its machine offset worked out for that machine's name as apply
    writes the line. "off": the instance turned it off. None when anything cannot be worked out: the caller keeps its
    old fixed numbers then."""
    try:
        from altention import config as altconfig, scheduler
        plugins, _ = altplugin.discover()
        d = next(d for n, _f, d in plugins if n == plugin_name)
        items, _ = altplugin.effective_schedules(plugin_name, d, altconfig.settings())
        raw = next(i for i in items if i.get("name") == item_name)
        if raw.get("unknown") or not isinstance(raw.get("when"), dict):
            return None
        if raw.get("enabled") is False:
            return {"off": True, "source": raw.get("source")}
        when = dict(raw["when"])
        after = when.pop("after", None)
        item = scheduler.parse({"items": [{"name": item_name, "when": when, "do": {"run": "x"},
                                           "timezone": raw.get("timezone")}]}, host=machine)["items"][0]
        if after and item["kind"] == "every":  # the core's lines: a fixed time after the machine's minute
            item["offset"] = (item["offset"] + scheduler.duration(after)) % item["every"]
        zone = scheduler.zone_for(item, scheduler.timezone()[0])
        if zone is None:
            return None
        trace = (raw.get("do") or {}).get("trace")
        within = WITHIN_DEFAULT
        if isinstance(trace, dict) and trace.get("within") is not None:
            w = trace["within"]
            within = scheduler.duration(w, "smh") if isinstance(w, str) else int(w)
        return {"item": item, "zone": zone, "within": within, "off": False, "source": raw.get("source")}
    except Exception:
        return None


def override_records(name, roles):
    """<plugin>.schedules for each plugin on `name` (and the core) whose schedules the instance overrides (C-37): ok
    with what runs now. An override that cannot be used, or a name that matches nothing, is said by
    plugins.would-apply from the machine's own report (every machine, the only one included); here it only keeps
    its item from being shown as in effect. Nothing when there is no override or the settings cannot be read."""
    try:
        from altention import config as altconfig
        settings = altconfig.settings()
        plugins, _ = altplugin.discover()
    except Exception:
        return []
    out = []
    for n, _f, d in plugins:
        if n != "core" and n not in roles:
            continue
        items, problems = altplugin.effective_schedules(n, d, settings)
        mine = [i for i in items if i.get("source") == "instance" and not i.get("unknown")]
        if mine:
            out.append(record("%s.schedules" % n, name, "ok", "", value="；".join(
                "%s 關掉了" % i["name"] if i.get("enabled") is False else "%s %s" % (i["name"], json.dumps(i["when"], ensure_ascii=False))
                for i in mine) + "（實例設定）"))
    return out


def core_stale(item, intervals):
    """Seconds without a good round of the sync or the engine update before it is said: three rounds of its line as
    that machine's apply read it from its crontab (intervals.json, from the probe), exactly as update.py and the sync
    there count them (update.stale_of); 45 minutes without it."""
    v = (intervals or {}).get(item) if isinstance(intervals, dict) else None
    return 3 * v if type(v) is int and 60 <= v <= 86400 else 45 * 60


def missed_two(sched, last, now):
    """C-37 (manager 2026-10-16): late only when two scheduled turns in a row went by with no record. The slots are
    those that should have finished by now (each given its within); `last` (seconds) older than the second newest of
    them, or no record at all, is late. None when it cannot be told."""
    from altention import scheduler
    slots = scheduler.recent_slots(sched["item"], now - sched["within"], sched["zone"], 2)
    if len(slots) < 2:
        return None
    return last is None or last < slots[1]


def first_wait(sched, added):
    """Minutes from a line added at `added` until its first turn should have finished (next slot, its within, some
    slack): what first_turn() waits before a line that never ran is a problem. None when it cannot be told."""
    from altention import scheduler
    slot = scheduler.next_slot(sched["item"], added, sched["zone"])
    return None if slot is None else int(-(-(slot + sched["within"] + FIRST_SLACK - added) // 60))


def iso_seconds(ts):
    """A record's ISO time as seconds, or None."""
    try:
        return datetime.fromisoformat(ts).timestamp() if ts else None
    except (TypeError, ValueError):
        return None


def engine_skills():
    """The skill folders under this engine's skills/ (install.sh links each one): what a probe that does not list its
    own engine's is checked against."""
    folder = os.path.join(ENGINE_DIR, "skills")
    try:
        return sorted(d for d in os.listdir(folder) if os.path.isdir(os.path.join(folder, d)))
    except OSError:
        return []


def skills_off(name):
    """The engine's skills whose plugin is turned off on machine `name` (disabled_on), or whose place there cannot be
    read (install.py does not make those: C-11; core.config says why): not missing there."""
    return altplugin.skills_off(name) | altplugin.skills_unsure(name)


SLEPT_MIN = 5 * 60  # seconds suspended since boot beyond which a machine counts as having slept

SECRETARY_FIX = ("在機器人那台的引擎跑 bin/altention dc post secretary --new-claude \"$(python3 planner/remind.py --secretary)\" "
                 "\"秘書：早報、回顧和提醒\"（開 #secretary，Claude 的工作目錄是秘書的資料夾，裡面有它的 CLAUDE.md），等一下再用 "
                 "python3 dcbot/tools/dcctl channels 查 #secretary 的 ID，寫進實例 plugins.json 的 \"planner\": {\"channel\": \"<ID>\"}")


def unset_channels(name):
    """[(plugin, setting, kind)]: the board channels ("kind": "board") and Claude channels ("kind": "claude", one a
    plugin wakes, e.g. planner's reminders) the plugins placed on machine `name` declare in plugin.json ("channels",
    with the instance setting that holds the channel's ID) whose setting is empty: such a plugin skips its round
    and says nothing (status, usage: the dashboard; owner's install 2026-10-15). [] when that cannot be worked out."""
    try:
        rows, _ = altplugin.on_this_machine(name)
        out = []
        for n, _f, d, where, _why in rows:
            for c in (d or {}).get("channels", []) if where == "on" else []:
                kind = c.get("kind") if isinstance(c, dict) else None
                key = c.get("setting") if kind in ("board", "claude") else None
                if isinstance(key, str) and key:
                    v = altplugin.config.setting(key)
                    if isinstance(v, bool) or not isinstance(v, (str, int)) or not str(v).strip():
                        out.append((n, key, kind))
        return out
    except Exception:
        return []


BACKED = {"instance": None, "logs": "chatlog", "progress": "progress", "planner": "planner"}  # remote.py name -> plugin
STALE_PUSH = 24 * 3600  # a commit waiting to be pushed for longer than a day: said (the rounds push every 15 minutes)


def backup_records(name, o, on):
    """C-25 (data-safety 2.5, 3): each backup on machine `name`, from ops/remote.py status run there: none (warn: the
    computer broken, it is lost), set but not attached (warn, with what to run), readable by anyone (fail: the
    pre-push hook stops every push), whether it is public could not be told (warn: pushed anyway, decision 6a), no
    hook, commits not pushed for a day, the last push; and the five private folders that are not 700. A part not
    on that machine is not said; one never made there yet with no remote set neither (nothing to lose yet)."""
    import remote
    out = []
    if o.get("backup_error"):
        return [record("backup.status", name, "unknown", "讀不出備份的狀態（%s）" % o["backup_error"],
                       "在 %s 上跑 python3 ops/remote.py status 看它說什麼" % name)]
    now = o.get("now") or time.time()
    loose = []
    for row in o.get("backup") or []:
        n = row.get("name")
        if n not in BACKED or (BACKED[n] is not None and BACKED[n] not in on):
            continue
        rid, label, folder, setting = "backup.%s" % n, remote.LABELS[n], row.get("folder") or "?", row.get("setting") or ""
        set_it = "在 %s 上跑 python3 ops/remote.py set %s %s（先檢查，沒過會說哪一項）" % (name, n, setting or "<private repo 網址>")
        if isinstance(row.get("mode"), int) and row["mode"] & 0o077:
            loose.append(folder)

        def put(status, msg, fix=""):
            out.append(record(rid, name, status, "%s：%s" % (label, msg), fix))
        if row.get("error"):
            put("unknown", "讀不出來（%s）" % row["error"], "在 %s 上跑 python3 ops/remote.py status" % name)
            continue
        if not row.get("repo"):
            if setting:
                put("warn", "設了遠端 %s，但 %s 還不是 git repo：還沒接上" % (setting, folder),
                    "在 %s 上跑 ./install.sh（會照設定從遠端 clone 回來）" % name)
            elif n == "instance":
                put("warn", "沒有備份：%s 不是 git repo" % folder, set_it)
            continue
        origin = row.get("origin") or ""
        if not origin:
            if setting:
                put("warn", "設了遠端 %s，但 %s 沒有接上（沒有 origin）：原因見 alerts 的 attach-refused 或 clone-refused" % (setting, folder), set_it)
            elif row.get("own"):
                put("warn", "用你自己指定的資料夾 %s（planner.folder），它沒有遠端：只在這台" % folder,
                    "在那個資料夾自己加一個 private 的遠端；或拿掉 planner.folder，改用預設的資料夾讓同步備份")
            else:
                put("warn", "沒有備份：只在這台，電腦壞了就救不回來", set_it + "；怎麼建 private repo 見 README 的「備份與還原」")
            continue
        if row.get("own"):  # planner.folder: theirs to back up; our hook would have the sync commit and push all of it
            if (row.get("check") or {}).get("verdict") == "public":
                put("fail", "%s 的 origin %s 沒有帳號也讀得到（公開的 repo）" % (folder, origin),
                    "到 repo 的主機把它改成 private（GitHub：Settings → General → Change visibility）")
            else:
                put("ok", "用你自己指定的資料夾 %s（planner.folder），推到 %s 由你自己管；同步不碰它" % (folder, origin))
            continue
        if setting and not remote.same_repo(origin, setting):
            put("warn", "%s 的 origin 是 %s，跟設定的 %s 不一樣：推到的是 origin" % (folder, origin, setting),
                "要用設定的那個：在 %s 上跑 python3 ops/remote.py set %s %s --replace" % (name, n, setting))
            continue
        verdict = (row.get("check") or {}).get("verdict")
        if verdict == "public":
            put("fail", "%s 沒有帳號也讀得到（公開的 repo）：推送都被擋下，沒有備份" % origin,
                "到 repo 的主機把它改成 private（GitHub：Settings → General → Change visibility），下一次推送會再檢查")
            continue
        if not row.get("hook"):
            put("warn", "%s 沒有我們的 pre-push hook：推送前不會檢查 repo 是不是變成公開的" % folder,
                "在 %s 上跑 python3 ops/remote.py hook %s" % (name, n))
            continue
        data = ((o.get("sync") or {}).get("data") or {}).get("planner/reminders") if n == "planner" else None
        if isinstance(data, dict) and data.get("ok") is False and data.get("error"):
            put("warn", "這一輪的同步沒成功：%s" % data["error"], "照它說的修；紀錄在 ~/.local/state/altention/cron-sync.log")
            continue
        last = row.get("last_push")
        when = time.strftime("%Y-%m-%d %H:%M", time.localtime(last)) if last else "沒有紀錄"
        waiting = row.get("ahead") if row.get("ahead") is not None else row.get("unpushed")
        if waiting and now - (row.get("waiting_since") or now) > STALE_PUSH:
            put("warn", "%d 個 commit 一天以上沒推上去（上次推送：%s%s）" % (
                waiting, when, "" if row.get("ahead") is not None else "；還沒有上游分支"),
                "看 alerts 和 ~/.local/state/altention/cron-sync.log 裡推送失敗的原因")
            continue
        unsure = "；沒辦法確認是不是公開的（連不上，或是只能用 ssh 連的主機），照樣推送" if verdict == "unknown" else ""
        others = remote.other_branches(row)
        moved = "；遠端原本的分支是 %s，之後推到 main" % "、".join(others) if others else ""
        put("warn" if unsure else "ok", "備份到 %s；上次推送：%s%s%s" % (origin, when, moved, unsure),
            "確認那個 repo 是 private" if unsure else "")
    if isinstance(o.get("usage_mode"), int) and o["usage_mode"] & 0o077 and "usage" in on:
        loose.append("~/.local/share/usage")
    out.append(record("backup.private", name, "warn" if loose else "ok",
                      "別的帳號讀得到：%s（應該是 700）" % "、".join(loose) if loose else "",
                      "在 %s 上跑 chmod 700 %s" % (name, " ".join(shlex.quote(f) if f[:1] != "~" else f for f in loose))))
    return out


def check(name, m, head, instance_repo=None):
    """The records of one machine. A problem's sentence is what doctor has always said; its fix is new."""
    out = []

    def say(check_id, problem, fix="", value=None):
        out.append(record(check_id, name, "fail" if problem else "ok", problem or "", fix, value))

    apply_it = "python3 ops/apply.py %s" % name
    eng = engine_raw(m)  # that machine's engine as its entry in the list says, else the default place
    sync_it = "在 %s 上跑 python3 %s/altention/cron_sync.py（紀錄在 ~/.local/state/altention/cron-sync.log）" % (name, eng)
    chatlog_it = "在 %s 上跑 python3 %s/chatlog/chatlog.py sync（紀錄在 ~/.cache/chatlog/chatlog.log）" % (name, eng)
    alias, rc, stdout, err = run_on(m, "ENGINE = %r  # this machine's engine (its entry in the list)\n" % engine_raw(m) + PROBE,
                                    timeout=120)
    # what runs here: the plugins placed on it, and the list's two roles that are not plugins (the bot, attaching)
    placed = plugins_on(name)
    on, unplaced = placed if placed is not None else (set(), {"chatlog", "planner", "progress", "usage"})
    roles, single = on | (set(m.get("roles", [])) & {"dcbot", "dcattach"}), bool(m.get("_single"))
    if rc != 0:
        say("core.reachable", "連不上（%s）" % str(err or rc)[:80], "確認 %s 開著，而且 ssh %s 連得上"
            % (name, " 或 ".join(a for a in m.get("ssh", []) if a != "local") or name))
        return out
    o, odd = tidy(json.loads(stdout.strip().splitlines()[-1]))
    if m.get("_bot_unlisted") and o.get("bot_env"):  # one machine, no list: the bot's settings here put it here
        roles.add("dcbot")
    say("core.reachable", "", value=alias)

    def first_turn(plugin, minutes, sched=None):
        """A plugin with no run on record yet, whose cron line apply added here: (minutes since, minutes left of
        `minutes`, its first turn plus some slack; 0 once past it), or None when apply's note does not say when.
        `sched` (schedule_of): the wait worked out from the line's time as it runs here (C-37), `minutes` else."""
        added = (o.get("cron_added") or {}).get(name)
        at, now = _num(added.get(plugin)) if isinstance(added, dict) else None, _num(o.get("now"))
        if at is None or now is None or now < at:
            return None
        if sched and not sched.get("off"):
            minutes = first_wait(sched, at) or minutes
        since = int((now - at) // 60)
        return since, max(0, minutes - since)

    def turn_late(sched, ts, fixed):
        """Whether a run last recorded at `ts` (ISO) is late: two turns in a row missed by the schedule as it runs
        here (missed_two, C-37); the old fixed `fixed` (a timedelta) when the schedule cannot be worked out."""
        told = missed_two(sched, iso_seconds(ts), time.time()) if sched and not sched.get("off") else None
        if told is not None:
            return told
        a = age(ts)
        return a is None or a > fixed

    def just(since):
        return "剛" if since < 1 else " %d 分鐘前" % since

    def since_change(plugin):
        """When `plugin`'s schedule line was added here or its code last changed, whichever is later (seconds), or None."""
        added = (o.get("cron_added") or {}).get(name)
        marks = [_num(added.get(plugin)) if isinstance(added, dict) else None, _num(o.get("%s_code_mtime" % plugin))]
        marks = [x for x in marks if x is not None]
        return max(marks) if marks else None

    def log_tail(plugin):
        """The output's last line, unless the output is older than since_change(): then it is what ran before
        (an update fixed what it said, and the new code has not had its turn yet: owner's update 2026-10-15)."""
        tail, written, mark = o.get("%s_log_tail" % plugin), _num(o.get("%s_log_mtime" % plugin)), since_change(plugin)
        return None if tail and written is not None and mark is not None and written < mark else tail

    # A machine that may sleep past that slack: shown, not a problem; it runs once it has been on a while. That is
    # always_on false, or the only machine with nothing saying it stays on (most new users' laptops) when a cron
    # daemon is seen running there (Linux; macOS cannot be told): WSL has the crontab command and no daemon, and its
    # lines never run. Past a day with no run at all, a problem whatever the machine (manager 2026-10-15).
    # The only machine with nothing saying it stays on is taken to have been asleep only on evidence, never on a
    # guess (owner's machine, on all along, was called asleep): it booted after the line was added (uptime), or it has
    # been suspended (Linux: CLOCK_BOOTTIME - monotonic, `slept`; a laptop's uptime counts its sleep). Where that
    # cannot be told (macOS), a note until a day has passed (#architecture-review on 1f91147).
    up, slept = _num(o.get("uptime")), _num(o.get("slept"))

    def never_ran(check_id, problem, fix, since):
        alone = bool(m.get("_alone")) and "always_on" not in m and o.get("cron_running") is True
        if since >= 24 * 60:
            say(check_id, problem, fix, "")
        elif m.get("always_on") is False:
            out.append(record(check_id, name, "warn", problem + "（這台不常開，開著一陣子後會跑）", fix, ""))
        elif alone and up is not None and up < since * 60:
            out.append(record(check_id, name, "warn", problem + "（這台 %d 分鐘前才開機，開著一陣子後會跑）" % (up // 60), fix, ""))
        elif alone and slept is not None and slept > SLEPT_MIN:
            out.append(record(check_id, name, "warn", problem + "（這台睡過，開著一陣子後會跑）", fix, ""))
        elif alone and slept is None:
            out.append(record(check_id, name, "warn", problem + "（看不出這台有沒有睡過；開著一陣子後會跑，一天後還沒有才算問題）", fix, ""))
        else:
            say(check_id, problem, fix, "")
    cron_first = "在 %s 上%s，再跑 %s/install.sh" % (name, INSTALL_CRON, eng)
    if o.get("crontab_missing"):  # apply.py writes nothing there without it: the fixes that say it are reworded at the end
        say("core.crontab", "沒有 crontab 指令，排程和 hook 都寫不進去", cron_first)
    elif o.get("cron_running") is False:  # WSL, containers: the lines are written and never run
        say("core.cron-service", "cron 服務沒在跑：排程寫進去了也不會跑", "在 %s 上%s，再跑一次 doctor" % (name, START_CRON))
    for plugin_name, key, kind in unset_channels(name):  # shown, not a problem: the plugin runs, only its channel waits
        section, _, field = key.partition(".")
        where = "寫進實例 plugins.json 的 \"%s\": {\"%s\": \"<頻道 ID>\"}" % (section, field)
        if kind == "board":
            out.append(record("%s.channel" % plugin_name, name, "warn", "看板頻道沒設定：%s（%s 的看板不會貼出來）" % (key, plugin_name),
                              "在機器人那台的引擎建一個頻道（python3 dcbot/tools/dcctl ensure dashboard），用 dcctl channels 查它的 ID，"
                              + where))
        elif plugin_name == "planner":  # the secretary has a channel of its own, working in its folder
            out.append(record("%s.channel" % plugin_name, name, "warn", "對話頻道沒設定：%s（%s 要叫醒的頻道，沒設就不會叫）" % (key, plugin_name),
                              SECRETARY_FIX))
        else:
            out.append(record("%s.channel" % plugin_name, name, "warn", "對話頻道沒設定：%s（%s 要叫醒的頻道，沒設就不會叫）" % (key, plugin_name),
                              "選一個有 Claude 的頻道，在機器人那台用 python3 dcbot/tools/dcctl channels 查它的 ID，"
                              + where))
    for plugin_name, needed in unmet_needs(name):  # a choice in the settings, not a fault: shown with how to undo it
        out.append(record("%s.needs" % plugin_name, name, "warn", "%s 開著，但它要用的 %s 在這台關掉了：%s 拿不到需要的資料"
                          % (plugin_name, needed, plugin_name),
                          "要 %s 就把 %s 打開：從實例 plugins.json 的 plugins.%s.disabled_on 拿掉 %s，再跑 %s；不要的話把 %s 也關掉"
                          % (plugin_name, needed, needed, name, apply_it, plugin_name)))
    if unplaced:  # shown, not a problem of the machine's: what decides it is what to look at
        out.append(record("core.placements", name, "warn", "插件在哪台跑算不出來：%s（這幾個不檢查，也不說它們該不該在這台）"
                          % "、".join(sorted(unplaced)), "python3 ops/plugins.py show %s 看原因" % name))
    out.append(record("core.probe-odd", name, "warn" if odd else "ok",
                      "這台回報的資料有 %d 項型別不對，當成沒有：%s" % (len(odd), "、".join(odd[:8])) if odd else "",
                      "那台的狀態檔可能壞了或被手改過：看 %s 上 ~/.local/state/altention/ 和 ~/.cache/chatlog/state.json 裡對應的檔" % name))
    archive = o["code"] == "archive"
    say("core.engine", "沒有 %s" % eng if o["code"] is None else
        "讀不到 %s 的 git 版本（.git 在，git 讀不出來）" % eng if o["code"] == "" else "",
        apply_it if o["code"] is None else "在 %s 上跑 git -C %s rev-parse HEAD 看 git 說什麼" % (name, eng),
        "壓縮檔" if archive else o["code"])
    if o["code"] and not archive and head and not single:  # an archive has no commit; nor may this engine
        say("core.engine-version", "程式碼 %s，不是最新的 %s" % (o["code"], head) if o["code"] != head else "",
            "等下一次同步（15 分鐘內）；或%s" % sync_it)
    # an item the instance turned off (plugins.<name>.schedules.<item>.enabled false, C-37): no line, no run, on purpose
    sched_off = set(p for p, i in (("chatlog", "sync"), ("progress", "run"), ("usage", "collect"))
                    if p in roles and (schedule_of(p, i, name) or {}).get("off"))
    for p, i in sorted((p, i) for p, i in (("chatlog", "sync"), ("progress", "run"), ("usage", "collect")) if p in sched_off):
        out.append(record("%s.cron" % p, name, "ok", "", value="關掉了（實例設定 plugins.%s.schedules.%s）" % (p, i)))
    out.extend(override_records(name, roles))
    if "chatlog" in roles:
        if "chatlog" not in sched_off:
            say("chatlog.cron", "" if o["cron_chatlog"] else "chatlog：沒有 cron", apply_it)
        say("chatlog.hooks", "" if o["hooks"] else "chatlog：沒有 hooks", apply_it)
        days = o["cleanup_days"] or 30
        say("chatlog.retention", "Claude Code 會刪除 %s 天前的對話" % days if days < 36500 else "", apply_it, days)
        a = age(o["chatlog_last_sync"])
        sched = schedule_of("chatlog", "sync", name)
        turn = first_turn("chatlog", 20, sched) if not o["chatlog_last_sync"] else None  # 20: its line every 15 minutes
        if "chatlog" in sched_off:
            pass  # no sync on a timer: hooks still save each conversation
        elif turn and turn[1]:  # never yet, the line added a moment ago: a note, not a problem
            out.append(record("chatlog.last-sync", name, "warn", "chatlog 還沒輪到第一次同步（排程%s裝上，%d 分鐘內會跑）"
                              % (just(turn[0]), turn[1]), chatlog_it, ""))
        elif turn:
            never_ran("chatlog.last-sync", "chatlog 排程裝上 %d 分鐘了，還沒有同步過" % turn[0], chatlog_it, turn[0])
        elif not o["chatlog_last_sync"]:  # when the line came is not known (added before apply noted it): shown only
            out.append(record("chatlog.last-sync", name, "warn", "chatlog 還沒有同步過", chatlog_it, ""))
        else:
            is_late = turn_late(sched, o["chatlog_last_sync"], timedelta(minutes=40))
            say("chatlog.last-sync", "chatlog 上次同步：%s" % fmt_age(a) if is_late and m.get("always_on", True) else "", chatlog_it,
                o["chatlog_last_sync"])
        say("chatlog.error", "chatlog 錯誤：%s" % ("…" + o["chatlog_error"][-80:] if len(o["chatlog_error"]) > 80 else o["chatlog_error"])
            if o["chatlog_error"] else "", chatlog_it)
        if o.get("chatlog_host"):  # chatlog's own name for this machine (its folder in the logs repo)
            out.append(record("chatlog.host", name, "ok", "", value=o["chatlog_host"]))
        unsure = o.get("chatlog_unsure") or []
        out.append(record("chatlog.unsure-segments", name, "warn" if unsure else "ok",
                          "對話紀錄有段檔但 meta.json 沒有完整的分段清單，這些對話不再 export、等人看：%s"
                          % "、".join(unsure[:3]) if unsure else "",
                          "照 docs/gotchas.md「對話紀錄的原始檔分段」的手動修法" if unsure else ""))
        big = o.get("chatlog_big") or []
        out.append(record("chatlog.big-file", name, "warn" if big else "ok",
                          "對話紀錄裡有檔案超過 50 MB（GitHub 超過 100 MB 就推不上去）：%s" % "、".join(
                              "%s（%.1f MB）" % (f, n / 1048576) for f, n in big[:3]) if big else "",
                          "在 %s 上跑 python3 %s/chatlog/chatlog.py split（原始紀錄分段＋壓縮）" % (name, eng) if big else ""))
    elif (o["cron_chatlog"] or o["hooks"]) and not single and "chatlog" not in unplaced:
        say("chatlog.not-listed", "plugins.json 沒有讓 chatlog 在這台跑，但這台還在跑",
            "%s 會移除；要留著就看實例 plugins.json 的 plugins.chatlog（disabled_on）" % apply_it)
    if "cron_dupes" in o:
        say("core.cron-duplicate", "crontab 裡同一個指令有兩行以上（會跑兩次）：%s" % "、".join(c[-60:] for c in o["cron_dupes"][:3])
            if o["cron_dupes"] else "", "在 %s 上跑 python3 ops/plugins.py plan 看哪幾行重複，手動刪掉多的那行（crontab -e）" % name)
    say("core.config", "設定讀不出來：%s" % o["config_error"][:150] if o.get("config_error") else "",
        "檢查 %s 上實例的 machines.json、plugins.json 是不是合法的 JSON" % name)
    if "skills" in o:  # every machine links the engine's skills (install.py), not only those that run chatlog
        # the folders under that machine's engine's skills/ (its probe), this engine's when the probe does not say
        missing = set(o.get("skills_expected") or engine_skills()) - set(o["skills"]) - skills_off(name)  # a plugin turned off: its skill too
        say("core.skills", "缺少 skill：%s" % ", ".join(sorted(missing)) if missing else "",
            "跑 altention apply %s（同步只補不在的連結；壞掉的、指到舊位置的連結要靠 apply 重建）" % name)
    if instance_repo:
        say("instance.joined", "" if o.get("instance") else "還沒接上實例", apply_it)
    sy = o.get("sync") or {}
    links = sy.get("links") or {}
    if o.get("instance") and sy:  # before the first sync there is nothing to say: it is what makes the links
        say("instance.memory-link", "已經有實例，但記憶資料夾還沒改指過去（等下一次同步，或手動跑 altention/cron_sync.py）"
            if links.get("memory") != "instance" else "", sync_it)
    if "backup" in o or "backup_error" in o:
        out += backup_records(name, o, on)
    elif o.get("instance") and o.get("instance_remote") == "":  # an engine from before ops/remote.py
        out.append(record("instance.remote", name, "warn", "實例沒有設定同步目的地：只在這台（不是故障）",
                          "要在多台共用時：git -C %s remote add origin <實例的 repo 網址>，再 git -C %s push -u origin main"
                          % (o["instance"], o["instance"])))
    if o.get("this_machine"):
        say("core.machine-name", "這台認為自己叫 %s，機器清單上叫 %s：認不出自己的名字時，插件在哪台跑都當成不知道，排程不增不刪"
            % (o["this_machine"], name) if o["this_machine"] != name else "",
            "在 %s 上把清單上的名字寫進 ~/.config/altention/host（或改清單的 hostname 欄位）" % name, o["this_machine"])
    if "last_ok" in o:  # 3a.10: three rounds of either, as update.py and the sync count them (C-37 C: at the interval set)
        for part, label in (("sync", "引擎和實例的同步"), ("update", "引擎更新（update.py）")):
            stale = core_stale("sync" if part == "sync" else "engine-update", o.get("intervals"))
            at = o["last_ok"].get(part)
            if at is None:
                if part == "update":
                    out.append(record("core.last-update-ok", name, "off", "引擎更新還沒啟用（3a.10 的 cron 行白天才加）",
                                      "等 3a.10 白天那步：apply 加上 update.py 那一行"))
                else:
                    turn = first_turn("core", 20, schedule_of("core", "sync", name))  # 20: its line every 15 minutes
                    if turn and not turn[1]:
                        never_ran("core.last-sync-ok", "%s排程裝上 %d 分鐘了，還沒有成功的紀錄" % (label, turn[0]), sync_it, turn[0])
                    else:
                        out.append(record("core.last-sync-ok", name, "warn", "%s還沒輪到第一次（排程%s裝上，%d 分鐘內會跑）"
                                          % (label, just(turn[0]), turn[1]) if turn else "%s還沒有成功的紀錄" % label, sync_it))
                continue
            since = (o.get("now") or at) - at
            if since > stale and not m.get("always_on", True):  # a laptop asleep: shown, not a fault, not told
                out.append(record("core.last-%s-ok" % part, name, "warn", "%s %d 分鐘沒有成功（這台不常開，不算故障）"
                                  % (label, since // 60), "等它開機；開著還是這樣就在那台跑一次同步", int(at)))
                continue
            say("core.last-%s-ok" % part, "%s已經 %d 分鐘沒有成功" % (label, since // 60) if since > stale else "",
                sync_it if part == "sync" else "在 %s 上跑 python3 %s/altention/update.py 看它說什麼" % (name, eng),
                int(at))
    for part, last in sorted((o.get("cron_logs") or {}).items()):
        if "不是這台的正式引擎" in last or "保底" in last:  # the cron line of this machine's engine said it did not run
            out.append(record("core.cron-%s-log" % part, name, "warn", "%s 那行 cron 最後說：%s" % (part, last[:200]),
                              "看那台的 ~/.local/state/altention/cron-%s.log" % part))
    if o.get("engine_unpacked"):  # shown, never told: it is how this engine was installed (manager 2026-10-14)
        out.append(record("core.update-unpacked", name, "warn", "引擎不是 git checkout（從壓縮檔解開的）：不會自動更新",
                          "要新版：下載新的壓縮檔解開到同一個位置再跑 install.sh；要自動更新：改用 git clone 安裝"))
    if o.get("update_available"):  # notify mode (the instance's core.update): shown, never told (manager 2026-10-14)
        tag = o["update_available"]["tag"][:40]
        out.append(record("core.update-available", name, "warn", "有新的發佈版本 %s（core.update 是 notify：不會自動更新）" % tag,
                          "要更新到它：跟 Claude 說「更新引擎」，或在 %s 的引擎跑 python3 altention/update.py --release、./install.sh、"
                          "python3 ops/doctor.py；要以後自動跟發佈版本：實例的 core.update 設成 release" % name, tag))
    if "deps_pending" in o:  # C-08: the engine moved on, the bot's packages did not come: the bot runs the old code
        dp = o["deps_pending"] or {}
        say("core.deps-pending", "引擎已經更新到 %s，但機器人要的套件沒裝上（%s）：機器人還在跑舊版" % (
            str(dp.get("head") or "")[:12] or "新版", " ".join(str(dp.get("why") or "原因沒有記下").split())[:160]) if dp else "",
            "在 %s 的引擎跑 dcbot/install.sh，再在 Discord 打 !restart（或等它自動再試：失敗後一小時一次）；"
            "機器在那之前重開的話，機器人會直接用新版啟動、少了套件就失敗，supervisor 會退回" % name)
    if "update_skipped" in o:  # rounds in a row that never compared with the upstream: the sync takes over, a person needs the why
        sk = o["update_skipped"] or {}
        mins = int(((o.get("now") or 0) - (sk.get("since") or o.get("now") or 0)) // 60) if sk else 0
        if sk and mins > 45 and not m.get("always_on", True):
            out.append(record("core.update-skipped", name, "warn", "引擎更新連續 %d 分鐘沒跑到比對上游：%s（這台不常開，不算故障）"
                              % (mins, sk.get("why")), "看那台的 ~/.local/state/altention/last_skip-update"))
        else:
            say("core.update-skipped", "引擎更新連續 %d 分鐘沒跑到比對上游：%s" % (mins, sk.get("why")) if sk and mins > 45 else "",
                "照原因處理（鎖：看誰拿著；殘局：在那台 git rebase --abort；fetch：看網路和金鑰），處理完下一輪就會恢復")
    if "update_blocked" in o:  # refused upstream: stays refused until a fix is pushed or apply.py is run by hand
        b = o["update_blocked"] or {}
        since = int(((o.get("now") or 0) - (b.get("since") or o.get("now") or 0)) // 60) if b else 0
        say("core.update-blocked", "上游被擋：%s %s（已經 %d 分鐘）" % (str(b.get("hash", ""))[:12], str(b.get("why", ""))[:160], since)
            if b.get("hash") else "",
            "在上游推修正，或確定檢查本身誤擋時，從控制端跑 python3 ops/apply.py %s（走 home-pull，不經過這個檢查）" % name)
    if "timezone" in o:  # 3a.11: shown, and a setting that cannot be used is a fault (the schedules that need it wait)
        tz = o["timezone"] or {}
        if tz.get("problem"):
            out.append(record("core.timezone", name, "fail", "排程用的時區不能用：%s（需要時區的排程項目先不做）" % tz["problem"][:160],
                              "修實例 plugins.json 的 core.timezone（例如 \"Asia/Taipei\"），或拿掉這個鍵改用系統時區"))
        else:
            out.append(record("core.timezone", name, "ok", "", "", "%s（%s）" % (tz.get("name") or "不知道", {"setting": "實例設定",
                              "system-default": "沒有設定，用系統時區"}.get(tz.get("source"), tz.get("source") or "來源不明"))))
    if (o.get("schedule_record") or {}).get("line"):  # record-only (3a.11): shown, never told (manager 2026-10-07)
        at = o["schedule_record"].get("round")
        mins = int(((o.get("now") or 0) - at) // 60) if at else None
        late = mins is None or mins > 10
        future = o["schedule_record"].get("future")
        ahead = "（另有時間在未來的記號，%d 分鐘後，不採信）" % ((future - (o.get("now") or 0)) // 60) if future else ""
        out.append(record("core.schedule-record", name, "warn" if late and m.get("always_on", True) else "ok",
                          ("排程器（只記錄）%s%s" % ("還沒有完整跑完一輪" if mins is None else "最後一輪完整跑完是 %d 分鐘前" % mins,
                                                ahead)) if late else "", "看那台的 ~/.local/state/altention/cron-schedule.log 和 scheduler/would-run.*.jsonl",
                          mins))
    twice = ["%s（兩行以上）" % k for k in o.get("cron_named_twice") or []]
    later = []  # an old line whose plugin's new lines are not written yet: alone for now (C-16)
    dead = []  # an old line whose program is gone (C-17 S4)
    rep_ = o.get("apply_report") if isinstance(o.get("apply_report"), dict) else {}
    pending = {it.get("plugin") for it in rep_.get("add") or [] if isinstance(it, dict) and it.get("kind") == "cron"}
    gone_items = [it for key in ("gone", "remove", "core") for it in rep_.get(key) or []
                  if isinstance(it, dict) and it.get("status") == "gone"]
    gone_old = ["%s 的%s：%s" % (it.get("plugin") or "?", "hook" if it.get("kind") == "hook" else "排程",
                                 str(it.get("installed") or "")[:90]) for it in gone_items]
    for it in (rep_.get("remove") or []) + (rep_.get("kept") or []):  # a line of ours a plugin on here no longer asks for
        if not isinstance(it, dict) or it.get("plugin") not in on or it.get("kind") == "hook" or it.get("status") == "gone":
            continue
        line = "%s 的舊行：%s" % (it["plugin"], str(it.get("installed") or "")[:100])
        if it.get("status") == "moved":  # the engine's old folder: apply takes it away as it writes the new lines
            later.append(line + "（搬家前的舊路徑，apply 會換掉）")
        elif it.get("status") == "extra" and any(str(it.get("installed") or "").strip()[:300] == d for d in o.get("cron_dead") or []):
            dead.append(line)  # its program is not there: it cannot run, so it cannot run twice either
        elif it.get("status") == "extra":
            (later if it["plugin"] in pending else twice).append(line)
    fix_twice = "在 %s 上先備份（crontab -l > ~/crontab.bak），再在引擎跑 ./install.sh：同一項的舊行會換成新的；還在的話用 crontab -e 刪掉舊的那行" % name
    if not twice and dead:
        out.append(record("plugins.cron-twice", name, "warn", "開著的插件留著舊行，但它們要跑的檔已經不在，跑不起來，不會重複：%s"
                          % "；".join((dead + later)[:4]), fix_twice))
    elif twice or not later:
        say("plugins.cron-twice", "crontab 裡同一個排程有兩行，或開著的插件留著一行它沒要的舊行（會跟新的一起跑，例如早報收兩次）：%s"
            % "；".join(twice[:4]) if twice else "", fix_twice)
    else:  # C-16: nothing runs twice yet; it would once the new lines are written beside a line apply keeps
        out.append(record("plugins.cron-twice", name, "warn", "開著的插件留著舊行，新行還沒寫上（現在還不會跑兩次）：%s"
                          % "；".join(later[:4]), fix_twice))
    gone_cron = sum(1 for it in gone_items if it.get("kind") != "hook")
    out.append(record("plugins.old-engine", name, "warn" if gone_old else "ok",
                      "%d 行排程、%d 個 hook 像是引擎搬家前留下的，但那個舊資料夾已經不在，apply 不會換也不會拿掉"
                      "（hook 指到不存在的檔，每個事件會出一行 hook error；排程跑不起來，不會重複）：%s"
                      % (gone_cron, len(gone_items) - gone_cron, "；".join(gone_old)) if gone_old else "",
                      "那是這個引擎搬家前的資料夾的話，從控制端跑 python3 ops/apply.py %s --moved-from <舊資料夾>，"
                      "一次全部換掉；不是的話那是別人的，留著" % name))
    if "apply_report" in o:  # 3a.12, report only
        try:
            out.append(would_apply_record(name, o.get("apply_report"), o.get("now") or 0, m.get("always_on", True),
                                          m.get("_alone", False)))
        except Exception as e:  # one machine's report must never stop the checks of the others (#code-review 2026-10-09)
            out.append(record("plugins.would-apply", name, "warn", "套用報告讀不懂（%s: %s）" % (type(e).__name__, str(e)[:120]),
                              "看那台的 ~/.local/state/altention/scheduler/apply-report.*.json"))
    if "instance_held" in o:  # held until a fix is pushed (taken by the next round) or apply.py takes it
        ih = o["instance_held"] or {}
        since = int(((o.get("now") or 0) - (ih.get("since") or o.get("now") or 0)) // 60) if ih else 0
        say("core.instance-held", "實例上游被擋：%s %s（已經 %d 分鐘）；這台的實例有 %s 個 commit 還沒推出去，等上游修好"
            % (str(ih.get("hash") or "?")[:12], str(ih.get("why", ""))[:160], since, ih.get("ahead", "?")) if ih else "",
            "在上游推修正（下一輪自動拉進來、自動推出去），或確定是檢查誤擋時，從控制端跑 python3 ops/apply.py %s"
            "（ops/instance.py pull --take-refused，不經過檢查）" % name)
    if "unchecked" in o:
        u = o["unchecked"]
        ok = (o.get("last_ok") or {}).get("update")
        if u and len(u) > 4 and u[4] is not None and ok is not None and ok > u[4]:
            u = None  # update.py worked since (its took-refused may not have been written: no record by then)
        say("core.took-unchecked", "update 沒在檢查時拉進了沒檢查過的版本（%s，%s）：%s" % (u[0], u[1], u[3][:200]) if u else "",
            "看 %s 的 update 為什麼停了；它恢復後第一輪會重新判斷（~/.local/state/altention/alerts、cron-update.log）" % name)
    for part, label in (("update", "引擎更新"), ("sync", "同步")):
        if "fallbacks" not in o:
            break
        # since then, a round that came to its end: for update.py any (a refusing round writes alive, not ok)
        fb, ok = o["fallbacks"].get(part), o.get("update_alive_at") if part == "update" else (o.get("last_ok") or {}).get(part)
        used = fb and (fb[4] is None or ok is None or fb[4] > ok)
        say("core.fallback-%s" % part, "%s跑不完，用了保底（%s，%s）：%s" % (label, fb[0], fb[1], fb[3][:200]) if used else "",
            "看 %s 的 ~/.local/state/altention/alerts 和 cron-%s.log；下一輪正常跑完就會消失" % (name, part))
    if o.get("alerts"):
        last = o["alerts"][-1]
        out.append(record("core.alerts", name, "warn", "這台最近 24 小時記下 %d 則要通知的事，最新一則（%s，%s）：%s"
                          % (len(o["alerts"]), last[0], last[1], last[3][:160]),
                          "在 %s 上看 ~/.local/state/altention/alerts（更早的也留在裡面；超過 24 小時就不再列在這裡）" % name,
                          len(o["alerts"])))
    if "stray_bot" in o:
        say("dcbot.stray-settings", "這台有機器人的設定（~/.config/dcbot/env），但機器清單說機器人在別台。如果機器人其實就在這台，表示它認不出自己"
            "在清單上的名字（它現在認為自己叫 %s；檢查 ~/.config/altention/host 或清單的 hostname 欄位）" % o.get("this_machine")
            if o["stray_bot"] else "", "先確認機器人在哪台：在這台的話，把清單上的名字寫進 ~/.config/altention/host；確定不在這台，再刪掉這台的 ~/.config/dcbot/env")
    for part, label in (("engine", "引擎同步"), ("instance", "實例同步")):
        if part in sy:
            err = (sy.get(part) or {}).get("error")
            say("sync.%s" % part, "%s失敗：%s" % (label, err[:150]) if err and err != "沒有設定遠端，不用更新" else "", sync_it)
    ahead = (sy.get("engine") or {}).get("ahead")
    backs = (sy.get("engine") or {}).get("rollbacks")  # on releases (core.update not branch): the supervisor's, kept here
    backs = backs if type(backs) is int and 0 <= backs else 0
    unsure = (sy.get("engine") or {}).get("rollbacks_unsure")  # a rollback's subject, more than a rollback in it
    unsure = unsure if type(unsure) is int and 0 <= unsure else 0
    if type(ahead) is int and ahead > 0 and backs >= ahead:  # C-07: what the rollback notice says, not "push it"
        out.append(record("sync.engine-ahead", name, "ok", "", "", "這台的退回 commit（%d 個）留在本機：core.update 不是 branch，"
                          "下一個發佈版本會取代它，不用推" % ahead))
    elif type(ahead) is int and ahead > 0 and (backs or unsure):  # rollbacks among them: never "push it" as it is
        out.append(record("sync.engine-ahead", name, "warn", "引擎有 %d 個這台的 commit 不在上游：同步不推引擎（不是故障）" % ahead,
                          "不要直接推：先請人看 git log（在 %s 上 git -C %s log @{u}..）是哪些。" % (name, eng)
                          + ("其中 %d 個是 supervisor 的退回 commit（dcbot: roll back…），不要推它們，留給下一個發佈版本取代；"
                             % backs if backs else "")
                          + ("其中 %d 個標題是退回（dcbot: roll back…）、內容卻不只是退回（可能被 amend 加了別的檔），"
                             "照原樣推出去等於把退回推上上游；" % unsure if unsure else "")
                          + "只有自己的、確定沒有私人值的 commit 才推（%s/bin/home-git push），退回壓在它們下面時要先處理掉" % eng,
                          ahead))
    elif type(ahead) is int and ahead > 0:  # C-31: the sync never pushes the engine; shown, not a problem (an int only:
        # 1e300 or NaN from a hand-edited file are numbers to JSON)
        out.append(record("sync.engine-ahead", name, "warn", "引擎有 %d 個這台的 commit 不在上游：同步不推引擎（不是故障）" % ahead,
                          "要推的話先在 %s 上 git -C %s log @{u}.. 看是哪些、確定裡面沒有私人的值，再跑 %s/bin/home-git push"
                          % (name, eng, eng), ahead))
    for part in ("engine", "instance"):
        for line in (sy.get(part) or {}).get("rescued") or []:
            say("sync.rescued", "被中斷的 rebase 救回來的修改還沒合併回去（git stash 或 .git/altention-saved/）：%s（%s）" % (line[:80], part),
                "在 %s 的%s資料夾（很多頻道共用，不要動別人的）：git stash 那種是 `git stash list` 裡訊息為"
                "「edits saved by an interrupted rebase」的那一筆，`git stash apply stash@{N}` 合併、確認後"
                "`git stash drop stash@{N}`（一定要帶編號）；.git/altention-saved/<資料夾> 那種，把要留的檔案複製回來後"
                "只刪那一個資料夾" % (name, "引擎" if part == "engine" else "實例"))
    twice = sorted(set(links.get("twice") or []) | set(o.get("kept_twice") or []))
    urgent = [t for t in twice if t.startswith("skills/council/data/")]  # council never reads the extra copy
    others = [t for t in twice if t not in urgent]
    merge = "在實例裡把帶機器名的那一份（檔名多一段 .<機器名>）併進正本，再刪掉它"
    if o.get("instance") or twice:
        say("instance.council-copies", "council 的資料有另存的副本（council 只讀正本，副本裡的內容等於沒生效），需要盡快合併：%s"
            % "、".join(urgent[:5]) if urgent else "", merge)
        say("instance.kept-twice", "搬到實例時有同名但內容不同的檔，兩份都留著，請合併：%s" % "、".join(others[:5]) if others else "", merge)
    if sy:
        say("sync.links", "改連結時出錯：%s" % links["error"][:150] if links.get("error") else "", sync_it)
        say("sync.config", "同步時設定讀不出來：%s" % sy["config_error"][:150] if sy.get("config_error") else "",
            "檢查 %s 上實例的 machines.json、plugins.json 是不是合法的 JSON" % name)
    for key, label in (("tmp_inodes", "檔案數"), ("tmp_space", "空間")):
        if key in o:
            say("core.%s" % key.replace("_", "-"), "/tmp 的%s用了 %d%%（滿了之後 Claude、機器人和測試都會出錯）" % (label, o[key])
                if (o[key] or 0) >= 80 else "", "清掉 %s 上 /tmp 裡不用的檔案（先用 du 或 find 看是誰佔的）" % name, o[key])
    if "entry_links" in o:  # C-17 S4: the command every instruction uses
        el = o["entry_links"]
        said_ = {"missing": "沒有", "file": "是一般檔案，不是連結", "broken": "指到不存在的檔", "old-name": "經過一個現在是連結的舊名字指到這個引擎，舊名字拿掉就斷",
                 "elsewhere": "指到另一個引擎的 altention", "other": "指到別的程式"}
        a_ = el.get("altention", "ok")
        bad = ["~/.local/bin/altention %s" % said_[a_]] if a_ in said_ else []
        if el.get("alt") in ("broken", "old-name", "elsewhere"):
            bad.append("~/.local/bin/alt %s" % said_[el["alt"]])
        status_ = "fail" if a_ in ("missing", "broken", "elsewhere") else ("warn" if bad else "ok")
        out.append(record("core.entry", name, status_, "；".join(bad) if bad else "",
                          "在 %s 的引擎資料夾跑 python3 bin/altention --link（一般檔案或別的程式的連結要先自己移開）" % name))
    if o.get("instance") and sy:  # as instance.memory-link: the first sync makes these links
        for path_name, state in (o.get("engine_paths") or {}).items():  # git no longer has it (after 2.3b) and no link stands there
            say("instance.engine-link.%s" % path_name.replace("/", "-"), "%s/%s 不是指向實例的連結（%s）；寫進去的東西不會同步"
                % (eng, path_name, "是一般資料夾" if state == "folder" else "不存在") if state in ("folder", "missing") else "",
                "不要手動搬：%s就好（它會把實例沒有的檔併過去，同名內容不同的兩份都留，再建連結）；跑完還在，"
                "看 sync.links 的錯誤，和實例 plugins.json 的 sync.owns 有沒有列 %s" % (sync_it, path_name), state)
    adopted = o.get("adopted") or []  # unpushed commits the sync took back after copying them into the instance
    say("sync.adopted", "引擎有 %d 個被同步拿掉的未推 commit（內容已併進實例，留作備份）：%s；確認實例裡有之後可刪："
        "git update-ref -d <ref>" % (len(adopted), "、".join(adopted[:3])) if adopted else "",
        "確認實例裡有那些內容後，在 %s 的引擎資料夾對每個 ref 跑 git update-ref -d <ref>" % name)
    say("dcbot.events-bad", "機器人有 %d 個處理失敗的事件在 dcbot/state/events-bad/（看 supervisor.out 的錯誤）" % o["events_bad"]
        if o.get("events_bad") else "", "看 %s 上 dcbot/state/supervisor.out 的錯誤；處理完把 events-bad/ 裡的檔案移走" % name)
    if "routes_without_pid" in o:  # shown, not told: such a pane still reports; nothing is failing
        _old = o.get("routes_without_pid") or []
        out.append(record("dcbot.routes-without-pid", name, "warn" if _old else "ok",
                          "路由表（dcbot/state/routes.json）有 %d 筆舊格式的路由沒有記窗格的行程（頻道 %s）：tmux server 重啟後窗格編號"
                          "重排，別的窗格可能被當成它" % (len(_old), "、".join(_old[:5])) if _old else "",
                          "在那些頻道 /detach 再 /attach 一次（%s 上的窗格），路由會照現在的格式重寫" % name if _old else ""))
    if "ownerless" in o and "dcbot" in roles:  # shown, not told: for the owner to decide, nothing is failing
        kept = (o.get("ownerless") or {}).get("kept")
        kept = [k for k in kept if isinstance(k, dict) and isinstance(k.get("name"), str)] if isinstance(kept, list) else []
        seen = _time((o.get("ownerless") or {}).get("at"))
        old = "（這份清單是 %s 寫的，機器人之後沒有再看過：可能已經不是現況）" % stamp(seen) \
            if seen is not None and o.get("now") and o["now"] - seen > 3600 else ""
        out.append(record("dcbot.ownerless-sessions", name, "warn" if kept else "ok",
                          "機器人的 tmux 上有 %d 個頻道已經不在的 Claude 對話沒有自動關掉（不確定能不能關）：%s%s" % (len(kept), "；".join(
                              "%s（原本的頻道 %s，閒置 %s 小時，%s）" % (k["name"], k.get("channel", "?"), k.get("idle_hours", "?"), k.get("why", "?"))
                              for k in kept[:5]), old) if kept else "",
                          "在 %s 上 tmux -L <機器人的 socket> attach -t <名字> 看它在做什麼；不要了就 tmux -L <機器人的 socket> kill-session -t <名字>"
                          "（對話內容還在，之後可以在它的資料夾 claude --resume）" % name, len(kept)))
    if "posts_failed" in o and "dcbot" in roles:  # shown, not told: the notification path is what is down
        n = o.get("posts_failed") or 0
        out.append(record("dcbot.posts-failed", name, "warn" if n else "ok",
                          "機器人有 %d 則通知（altention dc notify）送不出去，留在 dcbot/state/posts-failed/：指定的頻道、預設的通知頻道和 #audit 都沒送成"
                          "（頭 24 小時每 5 分鐘重試一次）" % n if n else "",
                          "看 %s 上 dcbot/state/posts-failed/ 裡每個檔的 text（通知的內容）和 dcbot/state/supervisor.out 的錯誤；"
                          "Discord 通了它們會自己送出並消失，超過 24 小時的讀完自己移走" % name, n))
    tp = o.get("typed_as_paste") or {}
    if "typed_as_paste" in o and "dcbot" in roles:  # shown for a week after the last time; a warning, not told
        at, days = _time(tp.get("at")), None
        if at is not None and o.get("now"):
            days = (o["now"] - at) / 86400.0
        recent = days is not None and -REPORT_AHEAD / 1440.0 <= days < 7  # dated ahead: not believed, as every mark
        odd = bool(tp) and (at is None or (days is not None and days < -REPORT_AHEAD / 1440.0))
        out.append(record("dcbot.typed-as-paste", name, "warn" if recent or odd else "ok",
                          ("機器人打的長訊息被 Claude Code 當成貼上（最近一次 %s，累計 %s 次）：長行會被包成貼上的內容、不被當成使用者的指示"
                           % (stamp(at), tp.get("count", "?")) if recent else
                           "dcbot/state/typed-as-paste.json 讀不懂（時間不是時間，或在未來）：不知道機器人打的長訊息有沒有被當成貼上") if recent or odd else "",
                          "Claude Code 的貼上門檻可能變了：照 docs/gotchas.md「打字的節奏」重量一次，調 altention/connectors/claude_code/typist.py 的 TYPE_CHUNK／TYPE_PAUSE"
                          "（改完刪掉 %s 上的 dcbot/state/typed-as-paste.json）" % name if recent else
                          "看 %s 上的 dcbot/state/typed-as-paste.json：不是機器人寫的樣子就刪掉它" % name))
    if "enter_differs" in o and "dcbot" in roles:  # shown, never told (manager 2026-10-09)
        times = (o.get("enter_differs") or {}).get("times")
        week = [t for t in times if _time(t) is not None and 0 <= (o.get("now") or 0) - t < 7 * 86400] if isinstance(times, list) else []
        out.append(record("dcbot.enter-differs", name, "warn" if len(week) >= ENTER_DIFFERS else "ok",
                          "機器人自己的對話裡，按 Enter 時輸入框的字跟它打的對不上，這一週 %d 次（最近 %s）：比對認不得 Claude Code 的某種畫法，或有人在那些對話裡打字"
                          % (len(week), stamp(max(week))) if len(week) >= ENTER_DIFFERS else "",
                          "看 %s 上 dcbot/state/supervisor.out 裡 [enter] 開頭的行是哪些頻道、打了多少字；是畫法的問題就補 dcbot/bot/events.py 的 same_typed" % name,
                          len(week)))
    if "dcbot" in roles:
        out.extend(bot_notes(name, o))
        sec = o.get("planner_secretary") if isinstance(o.get("planner_secretary"), dict) else None
        if sec and sec.get("folder") and sec.get("cwd") != sec.get("folder"):  # never changed for them: said
            out.append(record("planner.secretary", name, "warn",
                              "秘書提醒叫醒的頻道（planner.channel）%s，不是秘書自己的資料夾 %s：早報和回顧的說明（CLAUDE.md）和短期計畫不在它那裡。"
                              "可以把秘書搬到自己的頻道 #secretary" % ("工作目錄是 %s" % sec["cwd"] if sec.get("cwd") else "不是機器人管的 Claude 頻道",
                                                                 sec["folder"]),
                              SECRETARY_FIX + "；原本的頻道照舊，不用動"))
    shut = [d for d in o.get("open_dirs") or [] if d != "~/.cache"]
    say("core.private-dirs", "私人資料夾別的帳號讀得到（應該是 700）：%s" % "、".join(shut) if shut else "",
        "在 %s 上：chmod 700 %s" % (name, " ".join(shut)))
    if "~/.cache" in (o.get("open_dirs") or []):  # Claude Code's own installer makes it 755: a reminder, not a problem
        out.append(record("core.cache-open", name, "warn", "~/.cache 別的帳號讀得到（Claude Code 的安裝程式就是這樣建的，不是故障）；"
                          "chatlog 的資料也在裡面，這台有別的帳號就收起來", "在 %s 上：chmod 700 ~/.cache" % name))
    guide = ""
    if not o.get("global_md"):
        guide = "這台用的是自己的全域說明（~/.claude/CLAUDE.md 第一行不是 altention 的記號），不會收到引擎和實例的更新" \
            if o.get("global_md_state") == "own" else "~/.claude/CLAUDE.md 沒有連到共用的全域說明（%s）" % o.get("global_md_state", "?")
    say("core.global-guide", guide, ("要留的內容併進實例的 CLAUDE.global.md，刪掉 %s 上的 ~/.claude/CLAUDE.md，再%s" % (name, sync_it))
        if o.get("instance") else ("這台沒有實例：要留的內容併進引擎的 CLAUDE.global.md（或先建實例），刪掉 %s 上的 "
                                   "~/.claude/CLAUDE.md，再跑 %s" % (name, apply_it)), o.get("global_md_state"))
    edited = o.get("global_edited") or []
    say("core.global-guide-edited", "全域說明（~/.claude/CLAUDE.md）被改過、沒有覆寫，需要人看：實例的 global-notes-edited/ 有 %s。"
        "處理方式：要留的內容併進實例的 CLAUDE.global.md；刪掉 global-notes-edited/ 裡看過的那幾個檔"
        "（整個資料夾刪掉也可以，同步會提交刪除）；再刪掉 ~/.claude/CLAUDE.md，下一輪同步會重組" % "、".join(edited[:3]) if edited else "",
        "要留的內容併進實例的 CLAUDE.global.md，刪掉 global-notes-edited/ 裡看過的檔，再刪掉 ~/.claude/CLAUDE.md")
    if "dcbot" in roles:  # each step only means something once the one before it holds
        beat = o["bot_heartbeat_age"]
        steps = (("dcbot.settings", o["bot_env"], "機器人：還沒有 ~/.config/dcbot/env（token），不會啟動",
                  "在 %s 上，在自己的終端機跑 %s/install.sh discord（只問 token，其他的自己查；寫好就接著安裝那台）" % (name, eng)),
                 ("dcbot.cron", o["bot_cron"], "機器人：沒有排程", apply_it),
                 ("dcbot.supervisor", o["bot_supervisor"], "機器人：supervisor 沒在跑",
                  "等 cron 帶起來（5 分鐘內）；還是沒有就看 %s 上的 dcbot/state/supervisor.out" % name),
                 ("dcbot.heartbeat", beat is not None and beat <= 120, "機器人：沒有心跳（%s）"
                  % ("從未啟動" if beat is None else "%d 秒前" % beat), "機器人沒有心跳時 Discord 上的指令也沒有用：SSH 到 %s 照 dcbot/SUPERVISOR-RESCUE.md 做（先看 dcbot/state/supervisor.out）" % name))
        for check_id, holds, problem, fix in steps:
            said, how = problem, fix
            if check_id == "dcbot.heartbeat" and not holds:  # why it has none, when the supervisor's lines say it plainly
                started, ended = o.get("bot_started_age"), o.get("bot_last_exit")
                if started is not None and 0 <= started < BOT_CONNECTING:  # a run going, just begun: not yet a problem
                    out.append(record(check_id, name, "warn", "機器人剛啟動（%d 秒前），還在連上 Discord" % started,
                                      "過一兩分鐘再跑一次 doctor", ""))
                    break
                if ended in WRONG_LOGIN and started is None:  # a run going past that has its own story: not the last one's
                    said, how = WRONG_LOGIN[ended][0], WRONG_LOGIN[ended][1] % (name, eng)
            say(check_id, "" if holds else said, how)
            if not holds:
                break
    elif o.get("bot_cron") and not single:
        say("dcbot.not-listed", "清單上不負責 dcbot，卻有機器人排程", "%s 會移除；機器人要搬來這台就先改機器清單" % apply_it)
    if "progress" in roles and "progress" not in sched_off:
        problem = ""
        if o["cron_progress_paused"] and not o["cron_progress"]:
            problem = "進度：排程暫停中%s" % ("（正在跑）" if o["progress_running"] else "")
        elif not o["cron_progress"]:
            problem = "進度：沒有排程"
        say("progress.cron", problem, "%s（暫停中的話：把 %s 的 crontab 裡 progress/progress.py 那一行開頭的 # 拿掉）" % (apply_it, name))
        a = age(o["progress_last_run"])
        sched = schedule_of("progress", "run", name)
        stale = not o["progress_running"] and turn_late(sched, o["progress_last_run"], timedelta(minutes=30)) and o["cron_progress"]
        turn = first_turn("progress", 15, sched) if stale and not o["progress_last_run"] else None  # 15: every 10 minutes
        failed = o.get("progress_failed") or {}
        f_at, f_why = failed.get("at"), failed.get("why")
        if f_at and (not o["progress_last_run"] or (age(f_at) is not None and age(o["progress_last_run"]) is not None
                                                     and age(f_at) < age(o["progress_last_run"]))):
            say("progress.last-run", "進度最近一次執行失敗（%s）：%s" % (fmt_age(age(f_at)), f_why or "沒有說原因"),
                "照上面那句修；完整的輸出看 %s 上的 ~/.cache/progress/cron.out" % name, f_at)
        elif not o["progress_last_run"] and not o["progress_running"] and log_tail("progress"):
            # never finished a round (or stopped before it could note why), and its output says something
            say("progress.last-run", "進度還沒有跑完過一次，輸出的最後一行：%s" % log_tail("progress"),
                "照上面那句修；完整的輸出看 %s 上的 ~/.cache/progress/cron.out" % name, "")
        elif turn and turn[1]:  # never yet, the line added a moment ago: a note, not a problem
            out.append(record("progress.last-run", name, "warn", "進度還沒輪到第一次執行（排程%s裝上，%d 分鐘內會跑）" % (just(turn[0]), turn[1]),
                              "看 %s 上的 ~/.cache/progress/cron.out 和 ~/.cache/progress/progress.log" % name, ""))
        elif turn:
            never_ran("progress.last-run", "進度排程裝上 %d 分鐘了，還沒有執行過" % turn[0],
                      "看 %s 上的 ~/.cache/progress/cron.out 和 ~/.cache/progress/progress.log" % name, turn[0])
        else:
            say("progress.last-run", "進度上次執行：%s" % fmt_age(a) if stale else "",
                "看 %s 上的 ~/.cache/progress/cron.out 和 ~/.cache/progress/progress.log" % name,
                "running" if o["progress_running"] else (o["progress_last_run"] or ""))
        say("progress.errors", "進度上次有 %s 個錯誤" % o["progress_errors"] if o["progress_errors"] else "", "看 %s 上的 ~/.cache/progress/progress.log" % name)
    elif o["cron_progress"] and not single and "progress" not in unplaced and "progress" not in roles:
        say("progress.not-listed", "plugins.json 的 plugins.progress.on 不是這台，卻有進度排程（會兩台同時寫進度的資料）", "%s 會移除" % apply_it)
    if "usage" in roles and "usage" not in sched_off and not any(p == "usage" for p, _k, _w in unset_channels(name)):  # no channel: said above
        look = "完整的輸出看 %s 上的 ~/.cache/usage.out" % name
        a = age(o.get("usage_last_run"))
        failed = o.get("usage_failed") or {}
        f_at, f_why = (v if isinstance(v, str) else None for v in (failed.get("at"), failed.get("why")))
        # every hour, 7 minutes past: counted from the line added or the code changed, whichever is later (an update
        # long after the install starts the wait again: owner's update 2026-10-15)
        changed, now, turn = since_change("usage"), _num(o.get("now")), None
        sched = schedule_of("usage", "collect", name)
        if not o.get("usage_last_run") and changed is not None and now is not None and now >= changed:
            wait = (first_wait(sched, changed) if sched and not sched.get("off") else None) or 70  # 70: hourly at :07
            turn = (int((now - changed) // 60), max(0, wait - int((now - changed) // 60)))
        if f_at and (not o.get("usage_last_run") or (age(f_at) is not None and a is not None and age(f_at) < a)):
            say("usage.last-run", "用量統計最近一次收集失敗（%s）：%s" % (fmt_age(age(f_at)), f_why or "沒有說原因"),
                "照上面那句修；" + look, o.get("usage_last_run") or "")
        elif not o.get("usage_last_run") and log_tail("usage"):
            say("usage.last-run", "用量統計還沒有收集成功過，輸出的最後一行：%s" % log_tail("usage"), "照上面那句修；" + look, "")
        elif not o.get("usage_last_run") and turn and turn[1]:
            out.append(record("usage.last-run", name, "warn", "用量統計還沒輪到第一次收集（排程或程式%s裝上，%d 分鐘內會跑）"
                              % (just(turn[0]), turn[1]), look, ""))
        elif not o.get("usage_last_run") and turn:
            never_ran("usage.last-run", "用量統計的排程或程式裝上 %d 分鐘了，還沒有收集成功過" % turn[0], look, turn[0])
        elif not o.get("usage_last_run"):
            out.append(record("usage.last-run", name, "warn", "用量統計還沒有收集成功過（每小時一次）", look, ""))
        else:
            say("usage.last-run", "用量統計上次收集：%s" % fmt_age(a) if a is not None and turn_late(sched, o["usage_last_run"], timedelta(hours=3)) else "",
                look, o["usage_last_run"])
        if o.get("usage_errors"):
            out.append(record("usage.errors", name, "warn", "用量統計上次有 %d 台讀不到：%s" % (len(o["usage_errors"]), "；".join(o["usage_errors"][:3])),
                              "照上面那句修（別台要先在那台跑一次 usage/install-node.sh）", ""))
    if o.get("crontab_missing"):  # a whole fix of its own, never apply.py's command put inside another sentence
        for rec in out:
            if rec["id"] != "core.crontab" and rec["status"] == "fail" and apply_it in (rec.get("fix") or ""):
                rec["fix"] = "先處理「沒有 crontab 指令」那一項（%s），再跑一次 doctor" % cron_first
    return out


def privacy_record():
    """Personal information in the engine's committed tree (DESIGN.md 4.4): shown, never a problem. None when clean,
    when the scan cannot run, or when the instance has no privacy.json: only someone who publishes the engine keeps
    one, and without it the scan takes common words (a GitHub owner, an author's name) for personal ones."""
    try:
        from altention import config as altconfig
        if not os.path.isfile(os.path.join(altconfig.instance_dir() or "", "privacy.json")):
            return None
    except Exception:
        return None
    try:
        r = subprocess.run([os.path.join(ENGINE_DIR, "tools", "privacy-scan"), "HEAD", "--json"], stdout=subprocess.PIPE,
                           stderr=subprocess.DEVNULL, universal_newlines=True, timeout=120)
        found = ((json.loads(r.stdout).get("summary") or {}).get("personal") or {}) if r.stdout.strip() else {}
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
    if not found.get("total"):
        return None
    return record("core.privacy-scan", None, "warn", "個資掃描：引擎已提交的樹裡有 %d 筆、%d 個檔案（tools/privacy-scan HEAD 看明細）"
                  % (found["total"], found.get("files", 0)), "跑 tools/privacy-scan HEAD 看明細，把個人的內容搬到實例", found["total"])


def collect(names):
    """Every record: about the list, one machine after another, then about all of them together."""
    out = []
    try:
        inv = inventory()
        out.append(record("core.machine-list", None, "ok", "", value="single" if inv.get("single") else "list"))
    except Exception as e:  # a broken list: say so, and still look at this machine
        inv = {"machines": {"local": {"ssh": ["local"], "roles": []}}, "single": True}
        out.append(record("core.machine-list", None, "warn", "機器清單讀不出來：%s" % e,
                          "檢查實例的 machines.json 是不是合法的 JSON", "single"))
    machines = inv["machines"]
    if inv.get("single"):
        for m in machines.values():
            m["_single"] = True
    for m in machines.values():  # no list, or a list of one: nothing to place elsewhere
        m["_alone"] = len(machines) == 1
    nobody = not any("dcbot" in (m.get("roles") or []) for m in machines.values())
    for m in machines.values():  # no machine given the bot: it runs where its settings are (config.bot_machine)
        m["_bot_unlisted"] = nobody and "local" in (m.get("ssh") or [])
    unknown = [n for n in names if n not in machines]
    if unknown:
        sys.exit("清單裡沒有：%s" % ", ".join(unknown))
    names = names or list(machines)
    (head, head_why), repo = local_head_status(), (inv.get("instance") or {}).get("repo")
    if head_why:  # a .git git cannot read: the versions are not compared (an archive has none, and that is no fault)
        out.append(record("core.local-engine", None, "warn", "這台（跑 doctor 的）引擎%s；不比對各台的版本" % head_why,
                          "在這台跑 git -C %s rev-parse HEAD 看 git 說什麼" % ENGINE_DIR))
    with ThreadPoolExecutor(max_workers=len(names)) as ex:
        for records in ex.map(lambda n: check_safely(n, machines[n], head, repo), names):
            out += records
    if not inv.get("single"):
        running = [n for n in names if any(r["machine"] == n and (r["id"] == "progress.not-listed" or (r["id"] == "progress.cron" and r["status"] == "ok"))
                                           for r in out)]
        out.append(record("progress.single", None, "fail" if len(running) > 1 else "ok",
                          "進度同時排程在 %s，只能有一台" % ", ".join(running) if len(running) > 1 else "",
                          "實例 plugins.json 的 plugins.progress.on 只寫一台，再對其他台跑 python3 ops/apply.py <機器>"))
    scan = privacy_record()
    return out + ([scan] if scan else [])


BOT_CONNECTING = 180  # seconds: a bot run just begun that has no heartbeat yet is still logging in (the supervisor's trial)
WRONG_LOGIN = {  # how the bot's last run ended, as discord.py names it in supervisor.out: (problem, fix % (machine, engine))
    "login": ("機器人登入不了 Discord：token 不對（最近一次啟動是 LoginFailure，supervisor 會一直重試）",
              "token 不對：在 %s 上用 %s/install.sh discord 重新輸入（Developer Portal → 機器人（Bot）→ 重設權杖（Reset Token）拿新的）；"
              "機器人下一次重試就會用新的"),
    "intents": ("機器人連不上 Discord：Message Content Intent 沒開（最近一次啟動是 PrivilegedIntentsRequired）",
                "Developer Portal → 這個應用程式 → 機器人（Bot）→ Privileged Gateway Intents 打開 Message Content Intent 並存檔；"
                "%s 上的機器人下一次重試就會連上（引擎 %s）"),
}
ENTER_DIFFERS = 3  # times in a week the bot's own box differed from what it typed before doctor warns
REPORT_STALE = 45  # minutes: an apply report older than this is no report (one is worked out every 15)
REPORT_AHEAD = 5   # minutes: one dated further ahead than this is not believed (clocks differ by seconds, not minutes)


def report_odd(rep):
    """Why this apply report cannot be read as one of format 1, or "": every list it must have is there and holds
    what it should (a report with fields missing said "nothing to change"; a field of another type raised and
    took every machine's checks down with it: #code-review 2026-10-09)."""
    if rep.get("format") != 1:
        return "format 是 %r，這台的 doctor 只懂 1" % (rep.get("format"),)
    for key in ("add", "remove", "retime", "core"):
        if not isinstance(rep.get(key), list) or not all(isinstance(i, dict) for i in rep[key]):
            return "%s 不是項目的清單" % key
    for key in ("gone", "kept"):  # newer reports: kept apart from the lists above
        if key in rep and (not isinstance(rep[key], list) or not all(isinstance(i, dict) for i in rep[key])):
            return "%s 不是項目的清單" % key
    for key, inside in (("unknown", (list, tuple)), ("problems", str), ("disabled", str)):
        if not isinstance(rep.get(key), list) or not all(isinstance(i, inside) and i for i in rep[key]):
            return "%s 不在或內容不對" % key
    if rep.get("refuse") is not None and not isinstance(rep["refuse"], str):
        return "refuse 不是文字"
    return ""


def would_apply_record(name, rep, now, always_on=True, alone=False):
    """plugins.would-apply (3a.12): what the machine's own report says `apply --write` would change there. Nothing
    is applied automatically yet; this is what to look at before that is ever turned on. No report, or one too
    old, is said as that, never as "nothing to change" (a machine not always on: "not here", no warning). fail: a
    report that must not be written out (refuse), or something of the core's not as asked (never changed
    automatically: a person's to put right). warn: anything apply would add, remove or retime, a placement not
    known. A plugin turned off there on purpose is said as that, not as a fault. alone (the only machine): where a
    plugin runs not known yet is not said here (before the bot's token nothing places the bot's plugins; doctor's
    core.placements says it once)."""
    rep = rep if isinstance(rep, dict) else {}
    fix = "在那台看 python3 ops/plugins.py plan（只讀）；確定要改再 python3 ops/apply.py <機器>"
    at = _time(rep.get("at"))
    mins = int((now - at) // 60) if at is not None and now else None
    when = stamp(at, "沒有")
    if mins is not None and mins < -REPORT_AHEAD:  # a mark in the future is not believed (as the record-only round's)
        return record("plugins.would-apply", name, "warn", "套用報告的時間在未來（%d 分鐘後），不採信：不知道這台有沒有要改的" % -mins,
                      "看那台的時鐘，和 ~/.local/state/altention/scheduler/apply-report.*.json 是不是被改過", mins)
    if mins is None or mins > REPORT_STALE:
        if not always_on:
            return record("plugins.would-apply", name, "ok", "", "", "不在線（最後一份報告 %s）" % when)
        return record("plugins.would-apply", name, "warn", "沒有套用報告（最後一份：%s）：不知道這台有沒有要改的" % when,
                      "看那台的 ~/.local/state/altention/cron-schedule.log（每 15 分鐘算一次）", mins)
    if rep.get("failed"):
        return record("plugins.would-apply", name, "warn", "套用報告%s" % str(rep["failed"])[:200], fix, mins)
    odd = report_odd(rep)
    if odd:  # not ours to read: never "nothing to change"
        return record("plugins.would-apply", name, "warn", "套用報告讀不懂（%s）" % odd, "看那台的 ~/.local/state/altention/scheduler/"
                      "apply-report.*.json；那台的引擎可能比這台新或舊", mins)

    def names(items):
        return "、".join("%s/%s" % (i.get("plugin") or "?", i.get("name") or (i.get("installed") or "?")[:40]) for i in items[:6]) + (
            "…（共 %d 項）" % len(items) if len(items) > 6 else "")
    said = []
    for key, word in (("add", "會補"), ("remove", "會拿掉"), ("retime", "時間不同"), ("kept", "留著（帶我們的標記但沒有插件要，apply 不動）")):
        if rep.get(key):
            said.append("%s %d 項：%s" % (word, len(rep[key]), names(rep[key])))
    # the instance's schedule overrides that cannot be used (C-37): said on every machine, the only one included
    # (#code-review on c45394d: a typo there otherwise went unsaid on a one-machine install)
    bad = [u for u in rep.get("unknown") or [] if str(u[1]).startswith(("實例的設定不能用", "這一筆不用"))]
    if bad:
        said.append("實例的排程設定有 %d 筆不能用（apply 不動那幾行）：%s" % (len(bad), "；".join(
            str(u[1]).split("：", 1)[-1].split("；這一項現在的行不動")[0] if str(u[1]).startswith("實例的設定不能用")
            else "%s：%s" % (u[0], str(u[1]).split("：", 1)[-1]) for u in bad[:3])))
    other = [u for u in rep.get("unknown") or [] if u not in bad]
    if other and not alone:
        said.append("不知道 %d 項：%s" % (len(other), "、".join(str(u[0]) for u in other[:6])))
    if rep.get("problems"):
        said.append("問題：%s" % "；".join(str(p)[:120] for p in rep["problems"][:3]))
    if rep.get("gone"):  # kept by apply: nothing removed or added for them; plugins.old-engine says what to do
        said.append("%d 項像是引擎搬家前的舊行或 hook，但舊資料夾已經不在，apply 不動它們（見 plugins.old-engine）" % len(rep["gone"]))
    off = "刻意停用：%s" % "、".join(rep["disabled"]) if rep.get("disabled") else ""
    if rep.get("refuse") or rep.get("core"):
        bad = ([str(rep["refuse"])] if rep.get("refuse") else []) + (
            ["core 的 %d 項跟要的不一樣（永遠不會自動改，要人處理）：%s" % (len(rep["core"]), names(rep["core"]))] if rep.get("core") else [])
        return record("plugins.would-apply", name, "fail", "套用報告（只報告，沒有動手）：%s" % "；".join(bad + said)[:600], fix, mins)
    if said:
        return record("plugins.would-apply", name, "warn", "套用報告（只報告，沒有動手）：%s%s" % ("；".join(said)[:600], "；" + off if off else ""),
                      fix, mins)
    return record("plugins.would-apply", name, "ok", "", "", "沒有要改的%s" % ("（%s）" % off if off else ""))


def render(records, fixes=False):
    """What people read, from the records alone."""
    lines, names = [], []
    for r in records:
        if r["machine"] is not None and r["machine"] not in names:
            names.append(r["machine"])
    listed = next((r for r in records if r["id"] == "core.machine-list"), None)
    if listed and listed["status"] == "warn":
        lines.append("✗ %s\n只檢查這台。" % listed["msg"])
    if listed and listed.get("value") == "single":
        lines.append("（沒有機器清單：只有這台，不比對各機器版本）")
    lines.append("%-7s %-8s %-8s %-9s %-9s %-9s %s" % ("機器", "連線", "程式碼", "chatlog", "進度", "用量", "狀態"))
    for name in names:
        mine = {}
        for r in records:
            if r["machine"] == name:
                mine.setdefault(r["id"], r)
        value = lambda check_id: (mine.get(check_id) or {}).get("value")  # noqa: E731
        code = value("core.engine") or "—"
        cl = fmt_age(age(value("chatlog.last-sync"))) if "chatlog.last-sync" in mine else "—"
        prog = "—"
        if "progress.last-run" in mine:
            prog = "執行中" if value("progress.last-run") == "running" else fmt_age(age(value("progress.last-run")))
        use = fmt_age(age(value("usage.last-run"))) if value("usage.last-run") else "—"  # 上次收集
        bad = len([r for r in records if r["machine"] == name and r["status"] == "fail"])
        lines.append("%-9s %-10s %-10s %-11s %-11s %-11s %s" % (name, value("core.reachable") or "×", code, cl, prog, use,
                                                         "正常" if not bad else "有 %d 個問題" % bad))
    for r in records:
        if r["status"] == "warn" and r["id"] != "core.machine-list":
            lines.append("\n%s" % r["msg"])
    problems = [r for r in records if r["status"] == "fail" and r["machine"] is not None] + \
        [r for r in records if r["status"] == "fail" and r["machine"] is None]
    if problems:
        lines.append("\n問題：")
        for r in problems:
            lines.append("- %s：%s" % (r["machine"] or "全體", r["msg"]))
            if fixes and r["fix"]:
                lines.append("  修：%s" % r["fix"])
    else:
        lines.append("\n全部正常。")
    return "\n".join(lines)


def watch(records, whole=True):
    path = os.path.expanduser("~/.local/state/altention/cron-watch.log")
    try:  # the cron line appends this run's output there: kept to its last 500 lines
        with open(path) as f:
            rows = f.readlines()
        if len(rows) > 500:
            with open(path, "w") as f:
                f.writelines(rows[-500:])
    except OSError:
        pass
    return _watch(records, whole)


RECORDS = "doctor-records.json"  # in the state folder: the last whole round of --watch, for programs that show it


def keep_records(state_dir, records, now=None):
    """Leave a whole round's records (what --json prints) where a program on this machine can read them without
    asking every machine again: {"at": when, "records": [...]}, written whole or not at all (a reader never sees
    half a file). That it cannot be written is said and stops nothing: telling comes first."""
    path = os.path.join(state_dir, RECORDS)
    try:
        os.makedirs(state_dir, exist_ok=True)
        with open(path + ".tmp", "w", encoding="utf-8") as f:
            json.dump({"at": time.time() if now is None else now, "records": records}, f, ensure_ascii=False)
        os.replace(path + ".tmp", path)
        return True
    except (OSError, TypeError, ValueError) as e:
        print("doctor --watch：這一輪的結果寫不下來（%s: %s）；通知照常" % (type(e).__name__, e))
        return False


# What --watch tells (fail records only; DESIGN 7.1 lists why each is told and what stays a warn). A machine that is
# not always on is not told about for the ones that only mean it was asleep.
WATCHED = ("core.check-error", "core.last-sync-ok", "core.last-update-ok", "core.update-blocked", "core.update-skipped", "core.reachable",
           "core.took-unchecked", "core.fallback-update", "core.fallback-sync", "core.instance-held")
# warnings that are told all the same: a conversation export leaves alone has no backup from then on, someone has
# to look (manager 2026-10-08)
WATCHED_WARN = ("chatlog.unsure-segments",)
WATCHED_ASLEEP_OK = ("core.last-sync-ok", "core.last-update-ok", "core.update-skipped", "core.reachable")


def check_errors_told(altupdate, errors, looked_at, unreachable=()):
    """A machine that cannot be checked (core.check-error) is told when that begins, once, and once more when it
    can be checked again (not when it only cannot be reached now); not again while it lasts (manager 2026-10-09: not the once-in-six-hours of the others,
    and its end is said). Which machines are in that state is kept beside the alerts. A machine not looked at
    this round (--watch of some machines only) keeps its state."""
    path = os.path.join(altupdate.state_dir(), "watch-check-error.json")
    try:
        with open(path, encoding="utf-8") as f:
            was = json.load(f)
        was = {str(k): v for k, v in was.items() if _time(v) is not None} if isinstance(was, dict) else {}
    except (OSError, ValueError):
        was = {}
    now = dict(was)
    for machine, msg in errors.items():
        if machine not in was:  # a kind of its own each time it begins: alert()'s once-in-six-hours never holds it back
            now[machine] = int(time.time())
            altupdate.alert("watch", "%s/core.check-error@%d" % (machine, now[machine]), "%s：%s" % (machine, msg))
    for machine in sorted(was):
        if machine in looked_at and machine not in errors:
            del now[machine]
            if machine in unreachable:
                continue  # not checked again, only not reached now: "can be checked again" would say the opposite
                # (it went to the owner so, #code-review 2026-10-09); core.reachable tells what there is to tell
            altupdate.alert("watch", "%s/core.check-error-over@%d" % (machine, int(was[machine])),
                            "%s：doctor 又檢查得了這台了（從 %s 起檢查不了）" % (machine, stamp(was[machine])))
    if now != was:
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path + ".tmp", "w", encoding="utf-8") as f:
                json.dump(now, f)
            os.replace(path + ".tmp", path)
        except OSError:
            pass  # cannot be kept: told again next round (better twice than never)


def _watch(records, whole=True):
    """--watch (the bot's machine, its own core cron line from 3a.10 on, never the scheduler): every machine whose
    sync or engine updater has not worked for three rounds (the updater not yet enabled does not count), or that
    cannot be reached, is told from here, once in 6 hours each, so
    someone hears of it even when every machine's own telling is down (manager 2026-10-06)."""
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from altention import update as altupdate
    kind, why = altupdate.verdict()
    if kind == "other":  # from another checkout nothing is told and nothing written
        print("doctor --watch：這不是這台的正式引擎（%s），不看也不通知" % why)
        return 0
    if whole:  # a round of some machines only would pass for all of them: not kept
        keep_records(altupdate.state_dir(), records)
    try:
        machines = inventory().get("machines") or {}
    except Exception:
        machines = {}
    asleep_ok = {n for n, m in machines.items() if isinstance(m, dict) and m.get("always_on") is False}
    bad = [r for r in records if ((r["status"] == "fail" and r["id"] in WATCHED) or (r["status"] == "warn" and r["id"] in WATCHED_WARN))
           and not (r["id"] in WATCHED_ASLEEP_OK and r["machine"] in asleep_ok)]  # sleeping is no news; what came in is
    errors = {r["machine"]: r["msg"] for r in bad if r["id"] == "core.check-error"}
    for r in bad:
        if r["id"] != "core.check-error":
            altupdate.alert("watch", "%s/%s" % (r["machine"], r["id"]), "%s：%s" % (r["machine"], r["msg"]))
    check_errors_told(altupdate, errors, {r["machine"] for r in records if r["machine"]},
                      {r["machine"] for r in records if r["id"] == "core.reachable" and r["status"] != "ok"})
    # one line every round, so the log's last line is never old news (#code-review-2 2026-10-06)
    print("%s 看了 %d 台：%s" % (time.strftime("%Y-%m-%dT%H:%M:%S%z"), len({r["machine"] for r in records if r["machine"]}),
                                "、".join("%s %s" % (r["machine"], r["id"]) for r in bad) or "沒有要通知的"), flush=True)
    return 1 if bad else 0


def main(argv):
    flags = [a for a in argv if a.startswith("--")]
    unknown = [f for f in flags if f not in ("--json", "--fix", "--watch")]
    if unknown:
        sys.exit("doctor：不認得 %s（可以用 --json、--fix、--watch）" % " ".join(unknown))
    records = collect([a for a in argv if not a.startswith("--")])
    if "--watch" in flags:
        return watch(records, whole=not [a for a in argv if not a.startswith("--")])
    print(json.dumps(records, ensure_ascii=False, indent=1) if "--json" in flags else render(records, "--fix" in flags))
    return 1 if any(r["status"] == "fail" for r in records) else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
