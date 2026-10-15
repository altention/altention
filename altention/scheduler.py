"""The scheduler's reasoning: what a schedule file says, what is due this minute, what was already served.

Nothing here runs anything, installs anything or looks for schedule files: callers hand in the paths
(DESIGN.md 8, stage3a-plan.md 3a.11). Standard library only, Python 3.8 (which has no zoneinfo: time zones
are read from the system's zoneinfo files here).

A schedule file, written by the plugin (or its Claude) that owns it:

    {"late": "3h",                                   optional: the default for every item
     "items": [{"name": "morning", "enabled": true, "late": "1h",
                "when": {"at": "08:00", "days": "daily"},            or ["mon", "thu"]
                "do": {"message": "…", "channel": "project-a"}},     channel optional
               {"name": "collect", "when": {"every": "10m", "offset": "7m"},
                "do": {"run": "tools/collect", "output": "none", "timeout": 120, "env": {"A": "b"}}},
               {"name": "review", "when": {"once": "2026-10-08 09:00"}, "do": {"message": "…"}}]}

The older form is read as well: {"channel": "…", "items": [{"name", "enabled", "at", "days", "text"}]}.
A bare list of items is a schedule too (the "schedules" of a plugin.json).

- "offset": "machine" (with "every") spreads machines out: a minute worked out from the machine's name in the
  instance's machine list (a name that stays; a host name changes with the network on some machines). A
  machine without such a name goes at 0, and the item says why (offset_note).
- "timezone": "machine" beside "when" and "do": this item goes by the machine's own time zone.
- "log" in "do": the file its output is to be added to. Kept as written (~ is expanded by whoever uses it).
- "trace" in "do": a file that every run of the item brings up to date, so a run can be checked afterwards
  (cron_schedule.py --compare): a path (its modification time), or {"file": path, "key": "a.b",
  "within": "10m"} (the time written under that key of the JSON file: an ISO time or seconds since 1970).
  "within" is how long after the scheduled time the trace may be brought up to date (default 5m). Used only
  for that check: never read when serving, and one written wrong never stops the item.

- Times are wall-clock times in the instance's time zone (setting core.timezone). An instance without that
  setting goes by the machine's zone; a setting that is there but cannot be used stops the round (timezone()).
- "every" counts from local midnight: {"every": "10m", "offset": "7m"} is :07, :17, …; an interval that does
  not divide the day starts over at midnight. On the day the clocks change the count goes on in real time.
- An item is served once per scheduled time, the first minute at or after it, and not at all once it is more
  than `late` behind (the machine slept): 3h for "at" and "once" unless said, one interval for "every" (only
  the latest missed round is made up for). A "once" that is past its `late` is ignored for good.
- Only what the scheduler itself saw and then missed is made up for; no note is not "never done". An item
  with no note (a new install, an item just added, added again after being taken out, switched on again or
  given another time, the day it takes over from cron) is not served that round: this moment becomes where
  it starts from. One exception: a "once" first seen within 2 minutes after its time is served.
- A note too far ahead of now (the clock jumped ahead and came back) is started over as well: more than
  twice its interval for an "every" item, more than a day for the others.
- A schedule file that cannot be read stops only its own items; after 5 rounds in a row the owner is told
  which file of which plugin. A plugin.json is a schedule file too: its "schedules" are the items.
- A state file that is there but cannot be read is never taken for an empty one: that round does nothing
  and writes nothing. After 5 such rounds in a row the file is set aside (kept) and the state starts anew,
  every item seen for the first time.
- A zone that cannot be had stops only the items that mean a time of day ("at", "once"); "every" items and
  items on the machine's zone go on.
- Before an item is served it is claimed, under a lock: of two rounds at once, one serves it.
- "at" in the hour the clocks skip is served at the first minute that exists after it; in the hour that
  happens twice, the first time only.

What was served is kept per machine in ~/.local/state/altention/scheduler/ (machines may share a home):
sent.<machine>.json {"format": 2, "machine", "sent": {"<schedule file>#<item>": {"slot": the scheduled instant,
"state": "seen" | "off" | "claimed" | "done" | "failed", "at": when that was noted, "text": the slot as local
time, "when": what the item's schedule was, "attempts", "note"}}}.

A round, for whoever runs the items (nothing here does):
    r = start_round(paths, machine)                 # {"due", "fresh", "held", "gone", "not_mine", "problems",
                                                    #  "skipped", "notify", "zone"}
    for text in r["notify"]: ... tell the owner ...
    for d in r["due"]:
        try:
            mine = claim(machine, d["key"], d["slot"], tz=r["zone"], mark=d["item"]["mark"], retry=d["retry"])
        except (StateUnreadable, StateUnwritable): ... report, do not run; the round was not a good one ...
        if mine:
            ... run it ...
            finish(machine, d["key"], d["slot"], ok, note)
    round_done(machine)                             # only when the round went through; last_good_round() reads it
"""
import bisect
import calendar
import contextlib
import datetime
import fcntl
import hashlib
import json
import os
import re
import struct
import sys
import time

from . import config, plugin

DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
LATE_DEFAULT = 3 * 3600
LATE_MAX = 7 * 86400
LOG_MAX = 1024 * 1024  # bytes: a log is renamed to .1 beyond this, so it takes 2 MB at most
NOTIFY_AFTER = 5       # rounds in a row the state could not be read (or the zone had) before someone is told
NOTIFY_AGAIN = 86400   # seconds: and told again this often for as long as it goes on
RETRY_MAX = 3          # times an item with "retry" is tried for one scheduled time
PREVIEW = 3 * 3600     # seconds ahead switch_preview looks
ONCE_GRACE = 120       # seconds: a "once" first seen this soon after its time is still served
FUTURE_MAX = 86400     # seconds: a note later than now by more than this is a clock that jumped; start over
FUTURE_ROUNDS = 2      # ...for an "every" item: by more than this many of its intervals
STATES = ("seen", "off", "claimed", "done", "failed")
OUTPUTS = ("none", "post", "wake-on-output")
EPOCH = datetime.datetime(1970, 1, 1)


class ScheduleError(ValueError):
    """A schedule file (or a time zone) that cannot be used; the message says what is wrong, for its owner."""


class StateUnreadable(Exception):
    """The state file is there but cannot be read or understood. Never the same as "nothing was served"."""


class StateUnwritable(Exception):
    """The state could not be written (disk, permissions). What was to be noted is not noted."""


# ---------------------------------------------------------------------------------------------- time zones
def _epoch(naive):
    return calendar.timegm(naive.timetuple())


class Zone:
    """A time zone as one question, offset_at(instant) -> seconds east of UTC, and what follows from it."""
    name = "?"

    def offset_at(self, ts):
        raise NotImplementedError

    def local(self, ts):
        """The wall-clock time (a naive datetime) at an instant."""
        return EPOCH + datetime.timedelta(seconds=int(ts) + self.offset_at(ts))

    def resolve(self, naive):
        """The instant of a wall-clock time: the first of two when it happens twice, the first instant after
        it when the clocks skip it."""
        e = _epoch(naive)
        around = {self.offset_at(e - 86400), self.offset_at(e + 86400)}
        around |= {self.offset_at(e - o) for o in list(around)}
        valid = sorted(e - o for o in around if self.offset_at(e - o) == o)
        if valid:
            return valid[0]
        lo, hi = e - max(around), e - min(around)  # the change is in (lo, hi]
        while lo < hi:
            mid = (lo + hi) // 2
            if mid + self.offset_at(mid) >= e:
                hi = mid
            else:
                lo = mid + 1
        return lo

    def iso(self, ts):
        off = self.offset_at(ts)
        sign, off_abs = ("-" if off < 0 else "+"), abs(off)
        return "%s%s%02d:%02d" % (self.local(ts).strftime("%Y-%m-%dT%H:%M:%S"), sign, off_abs // 3600, off_abs % 3600 // 60)


class SystemZone(Zone):
    name = "system"

    def offset_at(self, ts):
        return time.localtime(ts).tm_gmtoff


class FixedZone(Zone):
    def __init__(self, seconds, name=None):
        self.seconds, self.name = seconds, name or "UTC%+d" % (seconds // 3600)

    def offset_at(self, ts):
        return self.seconds


_HMS = r"[+-]?\d{1,3}(?::\d{1,2}(?::\d{1,2})?)?"
_FOOTER = re.compile(r"^(?:<[^>]+>|[A-Za-z]{3,})(%s)(?:(?:<[^>]+>|[A-Za-z]{3,})(%s)?(?:,([^,]+),([^,]+))?)?$" % (_HMS, _HMS))


def _hms(text):
    sign = -1 if text.startswith("-") else 1
    parts = [int(p) for p in text.lstrip("+-").split(":")] + [0, 0]
    return sign * (parts[0] * 3600 + parts[1] * 60 + parts[2])


class _Rule:
    """The rule a zoneinfo file ends with ("CET-1CEST,M3.5.0,M10.5.0/3"): the years after its last listed change."""

    def __init__(self, text):
        m = _FOOTER.match(text)
        if not m:
            raise ValueError("TZ rule: %r" % text)
        self.std = -_hms(m.group(1))
        self.dst = -_hms(m.group(2)) if m.group(2) else self.std + 3600
        self.start, self.end = (self._date(m.group(3)), self._date(m.group(4))) if m.group(3) else (None, None)

    @staticmethod
    def _date(text):
        day, _, at = text.partition("/")
        return day, _hms(at) if at else 7200

    @staticmethod
    def _instant(year, rule, offset):
        day, seconds = rule
        if day.startswith("M"):
            month, week, dow = (int(x) for x in day[1:].split("."))
            first = datetime.date(year, month, 1)
            d = 1 + (dow - (first.weekday() + 1) % 7) % 7 + 7 * (week - 1)
            while d > calendar.monthrange(year, month)[1]:
                d -= 7
            date = datetime.date(year, month, d)
        elif day.startswith("J"):  # 1..365, 29 February never counted
            n = int(day[1:])
            date = datetime.date(year, 1, 1) + datetime.timedelta(days=n - 1 + (1 if calendar.isleap(year) and n > 59 else 0))
        else:  # 0..365, 29 February counted
            date = datetime.date(year, 1, 1) + datetime.timedelta(days=int(day))
        return calendar.timegm(date.timetuple()) + seconds - offset

    def offset_at(self, ts):
        if not self.start:
            return self.std
        year = (EPOCH + datetime.timedelta(seconds=int(ts) + self.std)).year
        start, end = self._instant(year, self.start, self.std), self._instant(year, self.end, self.dst)
        summer = start <= ts < end if start < end else not (end <= ts < start)
        return self.dst if summer else self.std


class FileZone(Zone):
    """A zone read from a zoneinfo (TZif) file: the changes it lists, then the rule it ends with."""

    def __init__(self, name, data, listed=True):
        self.name = name
        if data[:4] != b"TZif":
            raise ValueError("not a zoneinfo file")
        counts, pos, size = struct.unpack(">6l", data[20:44]), 44, 4
        if data[4:5] >= b"2":  # skip the 32-bit part, read the 64-bit one
            isut, isstd, leap, times, types, chars = counts
            pos += times * 5 + types * 6 + chars + leap * 8 + isstd + isut
            counts, pos, size = struct.unpack(">6l", data[pos + 20:pos + 44]), pos + 44, 8
        isut, isstd, leap, times, types, chars = counts
        self.at = list(struct.unpack(">%d%s" % (times, "q" if size == 8 else "l"), data[pos:pos + times * size]))
        pos += times * size
        index = list(data[pos:pos + times])
        pos += times
        kinds = [struct.unpack(">lBB", data[pos + i * 6:pos + i * 6 + 6]) for i in range(types)]
        pos += types * 6 + chars + leap * (size + 4) + isstd + isut
        self.offsets = [kinds[i][0] for i in index]
        self.before = next((k[0] for k in kinds if not k[1]), kinds[0][0] if kinds else 0)
        footer = data[pos:].strip().decode("ascii", "replace") if size == 8 else ""
        self.rule = _Rule(footer) if footer else None
        if not listed:  # tests: the rule alone
            self.at, self.offsets, self.before = [], [], self.rule.std

    def offset_at(self, ts):
        i = bisect.bisect_right(self.at, ts)
        if i == len(self.at) and self.rule:
            return self.rule.offset_at(ts)
        return self.offsets[i - 1] if i else self.before


def zone_dirs():
    return [d for d in (os.environ.get("TZDIR"), "/usr/share/zoneinfo", "/usr/lib/zoneinfo", "/usr/share/lib/zoneinfo",
                        "/etc/zoneinfo", "/var/db/timezone/zoneinfo") if d]


def zone(name):
    """The Zone of an IANA name ("Asia/Taipei"); ScheduleError when this machine does not know it."""
    if not isinstance(name, str) or not re.match(r"^[A-Za-z0-9_+\-]+(/[A-Za-z0-9_+\-]+)*$", name):
        raise ScheduleError("時區名稱不對：%r（要像 Asia/Taipei）" % (name,))
    for folder in zone_dirs():
        try:
            with open(os.path.join(folder, name), "rb") as f:
                found = FileZone(name, f.read())
            now = int(time.time())
            for ts in (now, now - 366 * 86400, now + 366 * 86400):  # a damaged file may only fail when asked
                if abs(found.offset_at(ts)) > 26 * 3600:
                    raise ValueError("offset")
            return found
        except (OSError, ValueError, struct.error, IndexError, TypeError, ArithmeticError, UnicodeError):
            continue
    raise ScheduleError("這台機器找不到時區 %s 的資料" % name)


_MISSING = object()


def timezone():
    """(zone, source, problem): the zone the schedules go by, and where it comes from.
    - The instance says core.timezone and this machine knows it: (zone, "setting", None).
    - The instance has no such setting at all (the state a new single machine is in): the machine's own
      zone, (zone, "system-default", None). That is the stated default, not a way out.
    - Anything else (settings that cannot be read, a name mistyped or unknown here, a value that is not
      text): (None, "setting", why). Nobody knows the zone then and nothing is to be served: going by the
      machine's zone instead would serve an 08:00 item at 16:00 on a machine kept in UTC."""
    try:
        core = config.settings().get("core", _MISSING)
    except config.ConfigError as e:
        return None, "setting", "實例設定讀不出來，不知道時區：%s" % e
    if core is _MISSING or (isinstance(core, dict) and "timezone" not in core):
        return SystemZone(), "system-default", None
    if not isinstance(core, dict):
        return None, "setting", "core.timezone 不能用：實例設定的 core 不是物件"
    try:
        return zone(core["timezone"]), "setting", None
    except ScheduleError as e:
        return None, "setting", "core.timezone 不能用：%s" % e


# ------------------------------------------------------------------------------------------ schedule files
def duration(text, units="mh"):
    """Seconds of "10m", "3h" (and "45s" where units allow it)."""
    m = re.match(r"^(\d+)([%s])$" % units, text) if isinstance(text, str) else None
    if not m:
        raise ScheduleError("時間長度要像 %s，不是 %r" % ("、".join("10" + u for u in units), text))
    return int(m.group(1)) * {"s": 1, "m": 60, "h": 3600}[m.group(2)]


def _clock(text):
    m = re.match(r"^(\d{1,2}):(\d{2})$", text) if isinstance(text, str) else None
    if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
        raise ScheduleError("at 要是 24 小時制的 HH:MM，不是 %r" % (text,))
    return int(m.group(1)) * 60 + int(m.group(2))


def _days(days):
    if days in (None, "daily"):
        return None
    if isinstance(days, list) and days and all(d in DAYS for d in days):
        return sorted({DAYS.index(d) for d in days})
    raise ScheduleError('days 要是 "daily" 或 mon…sun 的清單，不是 %r' % (days,))


def stable_name():
    """(name, why not): this machine's name in the instance's machine list, the one that stays the same
    whatever the network calls the machine today. (None, why) when the list cannot be read or does not have
    this machine (a single machine without a list is that case too)."""
    try:
        listed = config.inventory().get("machines") or {}
        name = config.name_of(config.stated_name()) or config.name_of(config.hostname())
    except config.ConfigError as e:
        return None, "機器清單讀不出來（%s）" % e
    if not listed:
        return None, "沒有機器清單"
    if name is None:
        return None, "這台不在機器清單上"
    return name, None


def hostname():
    """The name machines are spread out by: the stable one, or None. Tests replace this."""
    return stable_name()[0]


def machine_spread(every, host=None):
    """(seconds, why zero): how long after each round's start this machine goes: the md5 of its stable name,
    modulo 60, modulo the interval's minutes. host: a name to use instead (tests). Without a stable name
    the answer is a fixed 0, with the reason, for doctor to show."""
    why = None
    if host is None:
        host = hostname()
        if host is None:
            return 0, stable_name()[1] or "這台沒有穩定的機器名"
    minute = int(hashlib.md5(host.encode()).hexdigest(), 16) % 60
    return minute % max(1, every // 60) * 60, why


def machine_offset(every, host=None):
    """The seconds of machine_spread()."""
    return machine_spread(every, host)[0]


def _when(when, host=None):
    if not isinstance(when, dict):
        raise ScheduleError("when 要是物件")
    kinds = [k for k in ("at", "every", "once") if k in when]
    if len(kinds) != 1:
        raise ScheduleError("when 要有 at、every、once 其中一個（只能一個）")
    if kinds[0] == "at":
        return {"kind": "at", "at": _clock(when["at"]), "days": _days(when.get("days"))}
    if kinds[0] == "every":
        every = duration(when["every"])
        if not 60 <= every <= 86400:
            raise ScheduleError("every 要在 1m 到 24h 之間")
        by_machine, note = when.get("offset") == "machine", None
        if by_machine:
            offset, note = machine_spread(every, host)
        else:
            offset = duration(when["offset"]) if "offset" in when else 0
        if offset >= every:
            raise ScheduleError("offset 要比 every 小")
        return {"kind": "every", "every": every, "offset": offset, "offset_by": "machine" if by_machine else None,
                "offset_note": note}
    try:
        once = datetime.datetime.strptime(when["once"], "%Y-%m-%d %H:%M")
    except (TypeError, ValueError):
        raise ScheduleError("once 要像 2026-10-08 09:00，不是 %r" % (when["once"],))
    return {"kind": "once", "once": once}


def _do(do):
    if not isinstance(do, dict) or ("message" in do) == ("run" in do):
        raise ScheduleError("do 要有 message 或 run 其中一個（只能一個）")
    log = do.get("log")
    if log is not None and (not isinstance(log, str) or not log.strip()):
        raise ScheduleError("log 要是檔案路徑")
    if "message" in do:
        if not isinstance(do["message"], str) or not do["message"].strip():
            raise ScheduleError("message 不能是空的")
        channel = do.get("channel")
        if channel is not None and not isinstance(channel, (str, int)):
            raise ScheduleError("channel 要是名稱或 ID")
        return _traced(do, {"message": do["message"], "channel": None if channel is None else str(channel), "log": log,
                            "retry": False})
    if not isinstance(do["run"], str) or not do["run"].strip():
        raise ScheduleError("run 不能是空的")
    output = do.get("output", "none")
    if output not in OUTPUTS:
        raise ScheduleError("output 要是 %s 其中一個，不是 %r" % ("、".join(OUTPUTS), output))
    timeout = do.get("timeout")
    if isinstance(timeout, str):
        timeout = duration(timeout, "smh")
    if timeout is not None and (isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or timeout <= 0):
        raise ScheduleError("timeout 要是秒數或像 30s、10m 的長度")
    env = do.get("env") or {}
    if not isinstance(env, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in env.items()):
        raise ScheduleError("env 要是「名稱: 文字」的物件")
    return _traced(do, {"run": do["run"], "output": output, "timeout": timeout, "env": dict(env), "log": log,
                        "retry": False})


def _traced(do, parsed):
    """"trace" kept as written, unchecked: it is read only by --compare, which says what is wrong with it."""
    if "trace" in do:
        parsed["trace"] = do["trace"]
    return parsed


def _retry(do, parsed):
    if "retry" in do and not isinstance(do["retry"], bool):
        raise ScheduleError("retry 要是 true 或 false")
    parsed["retry"] = do.get("retry", False)
    return parsed


def _mark(when, timezone):
    """What an item's schedule is, as text: a note made for another schedule is not about this one."""
    parts = [when["kind"]]
    if when["kind"] == "at":
        parts += ["%d" % when["at"], "daily" if when["days"] is None else ",".join(DAYS[d] for d in when["days"])]
    elif when["kind"] == "every":
        parts += ["%d" % when["every"], "%d" % when["offset"]]
    else:
        parts.append(when["once"].strftime("%Y-%m-%d %H:%M"))
    return " ".join(parts + ([timezone] if timezone else []))


def _late(value, default):
    if value is None:
        return default
    late = duration(value)
    if late > LATE_MAX:
        raise ScheduleError("late 最多 %d 天" % (LATE_MAX // 86400))
    return late


def parse(data, host=None):
    """A schedule file's content (or a bare list of items) -> {"channel", "items": [item]}; ScheduleError
    naming the item at fault. An item: name, enabled, kind ("at" | "every" | "once") with its fields, late
    (seconds), timezone (None or "machine"), do (with retry), order, mark (its schedule as text), scheduler
    (False: read, never scheduled here; it stays in a cron line of its own). An item that runs the sync or
    the engine update without that mark is refused. Fields this version does not know are ignored.
    host: the name "offset": "machine" is worked out from (this machine's stable name when not given)."""
    if isinstance(data, list):
        data = {"items": data}
    if not isinstance(data, dict) or not isinstance(data.get("items"), list):
        raise ScheduleError("要有 items 清單")
    channel = data.get("channel")
    if channel is not None and not isinstance(channel, (str, int)):
        raise ScheduleError("channel 要是名稱或 ID")
    file_late = _late(data.get("late"), None)
    items, seen = [], set()
    for n, raw in enumerate(data["items"]):
        label = "第 %d 項" % (n + 1)
        try:
            if not isinstance(raw, dict):
                raise ScheduleError("要是物件")
            name = raw.get("name")
            if not isinstance(name, str) or not name.strip():
                raise ScheduleError("要有 name")
            label = "「%s」" % name
            if name in seen:
                raise ScheduleError("name 重複了")
            seen.add(name)
            if "when" in raw or "do" in raw:
                when, do = _when(raw.get("when"), host), _retry(raw.get("do") or {}, _do(raw.get("do")))
            # COMPAT: 還用舊格式（at、days、text）的排程檔；各插件的排程檔都改成 when 和 do 之後（階段 4）
            else:  # the older form: at, days, text beside the name
                if "text" not in raw:
                    raise ScheduleError("要有 when 和 do（舊格式是 at、days、text）")
                when = _when({"at": raw.get("at"), "days": raw.get("days")}, host)
                do = _do({"message": raw["text"]})
            if "message" in do and do["channel"] is None and channel is not None:
                do["channel"] = str(channel)
            default = file_late if file_late is not None else when["every"] if when["kind"] == "every" else LATE_DEFAULT
            if raw.get("timezone") not in (None, "machine"):
                raise ScheduleError('timezone 只能是 "machine"（照這台機器的時區），不寫就照實例的時區')
            if not isinstance(raw.get("enabled", True), bool):
                raise ScheduleError("enabled 要是 true 或 false，不是 %r" % (raw["enabled"],))
            if not isinstance(raw.get("scheduler", True), bool):
                raise ScheduleError("scheduler 要是 true 或 false，不是 %r" % (raw["scheduler"],))
            forbidden = getattr(plugin, "forbidden_run", None)  # the rule lives with the plugin files
            if forbidden is not None and forbidden(raw):
                raise ScheduleError('同步和引擎更新不進排程器；要留在自己的 cron 行就標 "scheduler": false')
            item = {"name": name, "enabled": raw.get("enabled", True), "do": do, "order": n,
                    "late": _late(raw.get("late"), default), "timezone": raw.get("timezone"),
                    "mark": _mark(when, raw.get("timezone")), "scheduler": raw.get("scheduler", True)}
            item.update(when)
            items.append(item)
        except ScheduleError as e:
            raise ScheduleError("%s：%s" % (label, e))
    return {"channel": None if channel is None else str(channel), "items": items}


def load(path, host=None):
    """parse() of a file; ScheduleError when it cannot be read or understood. A plugin.json (an object with a
    "name" or "schedules" and no "items") is read too: its "schedules" are the items, none when it has no
    such part, and the result's "plugin" is its name (None for a plain schedule file)."""
    try:
        with open(os.path.expanduser(path), encoding="utf-8") as f:
            data = json.load(f)
    except OSError as e:
        raise ScheduleError("讀不到（%s）" % (e.strerror or e))
    except ValueError as e:
        raise ScheduleError("不是正確的 JSON（%s）" % e)
    name = None
    if isinstance(data, dict) and "items" not in data and ("schedules" in data or isinstance(data.get("name"), str)):
        name = data["name"] if isinstance(data.get("name"), str) else None
        data = data.get("schedules", [])  # a plugin that schedules nothing: an empty schedule, not a broken file
        if not isinstance(data, list):
            raise ScheduleError("schedules 要是清單")
    out = parse(data, host)
    out["plugin"] = name
    return out


def describe(item):
    """One line about an item, for --check."""
    if item["kind"] == "at":
        when = "%02d:%02d %s" % (item["at"] // 60, item["at"] % 60,
                                 "daily" if item["days"] is None else ",".join(DAYS[d] for d in item["days"]))
    elif item["kind"] == "every":
        when = "every %s" % _span(item["every"]) + (" offset %s" % _span(item["offset"]) if item["offset"] else "")
        when += "（依機器%s）" % ("：" + item["offset_note"] + "，用 0" if item["offset_note"] else "") if item["offset_by"] else ""
    else:
        when = "once %s" % item["once"].strftime("%Y-%m-%d %H:%M")
    what = "message" if "message" in item["do"] else "run"
    return "%s：%s %s（%s，late %s%s）%s" % (item["name"], "開" if item["enabled"] else "關", when, what, _span(item["late"]),
                                        "，機器時區" if item["timezone"] else "",
                                        "" if item["scheduler"] else "（留在 cron，排程器不接手）")


def _span(seconds):
    return "%dh" % (seconds // 3600) if seconds and seconds % 3600 == 0 else "%dm" % (seconds // 60)


def needs_zone(item):
    """Does this item mean a time of day in the instance's zone? "at" and "once" do; "every" does not (it
    only counts), nor does an item that goes by the machine's zone."""
    return item["kind"] != "every" and item.get("timezone") != "machine"


def zone_for(item, tz):
    """The zone an item goes by: the machine's own when it says so, else the one given (the instance's).
    With no zone given (it is not known) an "every" item counts from the machine's midnight; an item that
    needs the zone has none (None)."""
    if item.get("timezone") == "machine" or (tz is None and not needs_zone(item)):
        return SystemZone()
    return tz


def latest_slot(item, now, tz):
    """The latest scheduled instant of an item at or before `now` (epoch seconds), or None."""
    if item["kind"] == "once":
        slot = tz.resolve(item["once"])
        return slot if slot <= now else None
    today = tz.local(now).date()
    if item["kind"] == "at":
        for back in range(LATE_MAX // 86400 + 2):
            day = today - datetime.timedelta(days=back)
            if item["days"] is not None and day.weekday() not in item["days"]:
                continue
            slot = tz.resolve(datetime.datetime.combine(day, datetime.time(item["at"] // 60, item["at"] % 60)))
            if slot <= now:
                return slot
        return None
    for back in (0, 1):  # every: counted from local midnight, in real time
        day = today - datetime.timedelta(days=back)
        start = tz.resolve(datetime.datetime.combine(day, datetime.time()))
        end = tz.resolve(datetime.datetime.combine(day + datetime.timedelta(days=1), datetime.time()))
        upto = min(now, end - 1)
        if upto >= start + item["offset"]:
            return start + item["offset"] + (upto - start - item["offset"]) // item["every"] * item["every"]
    return None


def due(item, last_run, now, tz):
    """Should this item be served now? The scheduled instant it is served for (epoch seconds), or None.
    last_run: the scheduled instant it was last served for (or the moment it was first seen). None, no note
    at all, is never due: what nobody saw being missed is not made up for. now: epoch seconds. tz: the
    instance's zone (an item with "timezone": "machine" goes by the machine's instead); None when it is not
    known: an item that needs it (needs_zone) is not due then, the others are judged as usual."""
    zone = zone_for(item, tz)
    if not item["enabled"] or last_run is None or zone is None:
        return None
    slot = latest_slot(item, now, zone)
    if slot is None or last_run >= slot or now - slot > item["late"]:
        return None
    return slot


def sent_key(path, name):
    """How an item is known in the state: the schedule file's real path and the item's name."""
    return "%s#%s" % (os.path.realpath(os.path.expanduser(path)), name)


def _plan(schedules, state, now, tz):
    """The judging of a round over files already read: [(path, schedule)]. Writes nothing. Returns
    {"due", "fresh", "held", "gone", "notes"} (see start_round)."""
    found, fresh, held, gone, notes, not_mine = [], [], [], [], [], []
    for n, (path, schedule) in enumerate(schedules):
        prefix = sent_key(path, "")
        names = {item["name"] for item in schedule["items"]}
        for key in state:
            if key.startswith(prefix) and key[len(prefix):] not in names:
                gone.append({"key": key, "path": path, "was": state[key]})
        for item in schedule["items"]:
            key = sent_key(path, item["name"])
            was = state.get(key)
            entry = was
            if not item["scheduler"]:
                not_mine.append({"key": key, "path": path, "item": item})
            if not item["enabled"] or not item["scheduler"]:  # neither is followed: taken up later, it is new
                if entry is None or entry["state"] != "off":
                    fresh.append({"key": key, "path": path, "item": item, "state": "off", "was": was})
                continue
            limit = FUTURE_ROUNDS * item["every"] if item["kind"] == "every" else FUTURE_MAX
            ahead = entry is not None and entry["slot"] - now > limit
            if ahead:
                notes.append((path, "「%s」的紀錄比現在晚 %s 以上（時鐘跳過？），重設起點，從現在開始算"
                              % (item["name"], _span(limit))))
            if ahead or entry is None or entry["state"] == "off" or entry.get("when") not in (None, item["mark"]):
                new = {"key": key, "path": path, "item": item, "state": "seen", "was": was}
                fresh.append(new)
                zone = zone_for(item, tz)
                if item["kind"] == "once" and zone is not None and not ahead:
                    slot = zone.resolve(item["once"])
                    if 0 <= now - slot <= ONCE_GRACE:  # written a moment before its time: still served
                        new["start"] = slot - 1
                        found.append({"key": key, "path": path, "item": item, "slot": slot, "_file": n, "retry": False})
                    elif slot < now:
                        notes.append((path, "「%s」這一筆 once（%s）第一次見到時已經過期，不會發"
                                      % (item["name"], item["once"].strftime("%Y-%m-%d %H:%M"))))
                continue
            if tz is None and needs_zone(item):
                held.append({"key": key, "path": path, "item": item})
                continue
            again = (entry["state"] == "failed" and item["do"]["retry"] and entry.get("attempts", 1) < RETRY_MAX)
            slot = due(item, entry["slot"] - 1 if again else entry["slot"], now, tz)
            if slot is not None and (not again or slot >= entry["slot"]):
                found.append({"key": key, "path": path, "item": item, "slot": slot, "_file": n,
                              "retry": again and slot == entry["slot"]})
    found.sort(key=lambda d: (d["slot"], "run" in d["item"]["do"], d["_file"], d["item"]["order"]))
    for d in found:
        del d["_file"]
    return {"due": found, "fresh": fresh, "held": held, "gone": gone, "notes": notes, "not_mine": not_mine}


def _load_all(paths, host):
    schedules, problems = [], []
    for path in paths:
        try:
            schedules.append((path, load(path, host)))
        except ScheduleError as e:
            problems.append((path, str(e)))
    return schedules, problems


def _judge(paths, state, now, tz, host, loaded=None):
    schedules, problems = loaded if loaded is not None else _load_all(paths, host)
    out = _plan(schedules, state, now, tz)
    out["problems"] = list(problems) + out.pop("notes")
    if out["held"]:
        out["problems"].append(("core.timezone", "不知道時區，這些項目先不做：%s" % "、".join(d["item"]["name"] for d in out["held"])))
    return out


def plan(paths, state, now, tz, host=None):
    """(due, problems, fresh) over several schedule files; nothing is written.
    due: [{"key", "path", "item", "slot", "retry"}] in the order to serve them: by scheduled time, messages
    before programs at the same time, then as written. problems: [(path, why)]: files that cannot be used
    (the other files still count), and things said once (a "once" already past when first seen, a note from
    a clock that jumped). fresh: [{"key", "path", "item", "state", "was"}], items without a usable note
    (never seen, switched on again, given another schedule, noted too far ahead: state "seen") or
    switched off (state "off"): to be noted with mark_seen(); not served, except a "once" seen within
    ONCE_GRACE seconds after its time, which is in due as well (its fresh entry says where it "start"s).
    state: what read_state() gave ({key: entry}).
    tz None (the zone is not known): items that need it ("at", "once") are left alone and named in one
    problem; "every" items and items on the machine's zone are judged as usual."""
    j = _judge(paths, state, now, tz, host)
    return j["due"], j["problems"], j["fresh"]


# -------------------------------------------------------------------------------------------------- state
def state_dir():
    """~/.local/state/altention/scheduler. Worked out each time: HOME may change (tests)."""
    return os.path.join(os.path.expanduser("~/.local/state/altention"), "scheduler")


def _machine_file(prefix, machine, suffix):
    return os.path.join(state_dir(), "%s.%s%s" % (prefix, re.sub(r"[/\\\0]", "_", machine), suffix))


def sent_path(machine, dry=False):
    """One file per machine (machines may share a home); record-only runs keep theirs apart."""
    return _machine_file("dry" if dry else "sent", machine, ".json")


def log_path(machine, kind="would-run"):
    """would-run: what the record-only mode would have served; problems: rounds that could not be done."""
    return _machine_file(kind, machine, ".jsonl")


def unreadable_path(machine, dry=False, kind="state"):
    """The count of rounds in a row that went wrong: kind "state" (state unreadable) or "zone" (zone unknown)."""
    return _machine_file({"state": "unreadable", "zone": "nozone"}[kind] + ("-dry" if dry else ""), machine, ".json")


def _read_entries(path):
    """{key: entry} of a state file; {} when there is none; StateUnreadable for anything else."""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as e:
        raise StateUnreadable("%s 存在但讀不出來（%s）" % (path, e))
    sent = data.get("sent", {}) if isinstance(data, dict) else None
    if not isinstance(sent, dict):
        raise StateUnreadable("%s 的內容不是排程器的狀態" % path)
    out = {}
    for key, value in sent.items():
        if (isinstance(value, dict) and isinstance(value.get("slot"), int) and not isinstance(value["slot"], bool)
              and value.get("state") in STATES):
            out[key] = dict(value)
            continue
        raise StateUnreadable("%s 裡「%s」的紀錄看不懂" % (path, key))
    return out


def read_state(machine, dry=False):
    """{key: entry} for a machine; {} when it has no state yet; StateUnreadable when the file is there but
    cannot be read (never taken for empty)."""
    return _read_entries(sent_path(machine, dry))


def read_sent(machine, dry=False):
    """{key: epoch of the scheduled time last noted}; raises as read_state() does."""
    return {key: entry["slot"] for key, entry in read_state(machine, dry).items()}


def _text(ts, tz):
    return (tz or FixedZone(0)).iso(ts)


def _update(machine, dry, change):
    """Read the state again under a lock, let change(entries) alter it and say (result, changed), write it to
    a temporary file and move that into place. An unreadable state raises StateUnreadable before anything
    is written: it is never replaced by what this round knows."""
    path = sent_path(machine, dry)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path + ".lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        entries = _read_entries(path)
        result, changed = change(entries)
        if changed:
            tmp = "%s.%d.tmp" % (path, os.getpid())
            try:
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump({"format": 2, "machine": machine, "sent": {k: entries[k] for k in sorted(entries)}},
                              f, ensure_ascii=False, indent=1)
                    f.write("\n")
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp, path)
            except OSError:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
                raise
    return result


def mark_sent(machine, served, tz=None, dry=False, now=None):
    """Note items as served without claiming: {key: scheduled epoch}. A later time wins. Returns {key: slot}."""
    at = int(time.time()) if now is None else int(now)

    def change(entries):
        changed = False
        for key, slot in served.items():
            if key not in entries or slot >= entries[key]["slot"]:
                kept = {k: v for k, v in entries.get(key, {}).items() if k == "when"}
                entries[key] = dict(kept, slot=int(slot), state="done", at=at, text=_text(slot, tz))
                changed = True
        return {k: e["slot"] for k, e in entries.items()}, changed
    return _update(machine, dry, change)


def mark_seen(machine, fresh, now, tz=None, dry=False, gone=()):
    """Note where items start from (a round's fresh): from `now` on they count; nothing before is made up.
    And drop the notes of items no longer in their schedule file (gone): back again, they are new.
    Each one is judged again under the lock: it is touched only if its note is still what this round saw
    ("was"; nothing, when the round saw none). A round that was slow must not put an item another round has
    served meanwhile back to a starting point: the same time would be served again. Returns how many notes
    were changed."""
    def change(entries):
        changed = 0
        for d in fresh:
            if entries.get(d["key"]) != d.get("was"):
                continue  # someone moved it on since this round read the state
            start = int(d.get("start", now))
            entries[d["key"]] = {"slot": start, "state": d["state"], "at": int(now), "text": _text(start, tz),
                                 "when": d["item"]["mark"]}
            changed += 1
        for d in gone:
            if d["key"] in entries and entries[d["key"]] == d.get("was"):
                del entries[d["key"]]
                changed += 1
        return changed, bool(changed)
    return _update(machine, dry, change)


def claim(machine, key, slot, now=None, tz=None, dry=False, mark=None, retry=False):
    """Take an item's scheduled time before serving it. True: it is this caller's to serve. False: someone
    has it or had it (a note for this time or a later one is there). Do not serve unless True.
    Raises StateUnreadable (the state cannot be read) or StateUnwritable (the claim could not be written):
    nobody has it, and it must not be served either; the round reports it.
    retry: a time whose run failed may be taken again (RETRY_MAX times in all)."""
    at = int(time.time()) if now is None else int(now)

    def change(entries):
        old = entries.get(key)
        attempts = 1
        if old is not None and old["slot"] >= slot:
            again = retry and old["slot"] == slot and old["state"] == "failed" and old.get("attempts", 1) < RETRY_MAX
            if not again:
                return False, False
            attempts = old.get("attempts", 1) + 1
        entry = {"slot": int(slot), "state": "claimed", "at": at, "text": _text(slot, tz), "attempts": attempts}
        when = mark if mark is not None else (old or {}).get("when")
        if when is not None:
            entry["when"] = when
        entries[key] = entry
        return True, True
    try:
        return _update(machine, dry, change)
    except OSError as e:
        raise StateUnwritable("%s 寫不進去（%s）" % (sent_path(machine, dry), e.strerror or e))


def finish(machine, key, slot, ok, note="", now=None, dry=False):
    """Say how a claimed run went. A failure stays on record ("failed", with the note); whether it is tried
    again is the item's "retry". False when the note is no longer about this time, or cannot be written:
    the item then stays "claimed", is not served again, and shows in stuck()."""
    at = int(time.time()) if now is None else int(now)

    def change(entries):
        entry = entries.get(key)
        if entry is None or entry["slot"] != slot or entry["state"] != "claimed":
            return False, False
        entry.update(state="done" if ok else "failed", at=at)
        entry.pop("note", None)
        if note:
            entry["note"] = str(note)[:500]
        return True, True
    try:
        return _update(machine, dry, change)
    except (StateUnreadable, OSError):
        return False


def forget(machine, path, dry=False):
    """Drop every note about one schedule file's items; whatever is scheduled there later is new. For the
    caller that knows the file's plugin has been switched off: the scheduler never does this by itself (a
    file that was merely not handed in, or a plugin nobody could read the state of, keeps its notes).
    Returns how many notes were dropped. Raises StateUnreadable or StateUnwritable; nothing is dropped then."""
    prefix = sent_key(path, "")

    def change(entries):
        mine = [key for key in entries if key.startswith(prefix)]
        for key in mine:
            del entries[key]
        return len(mine), bool(mine)
    try:
        return _update(machine, dry, change)
    except OSError as e:
        raise StateUnwritable("%s 寫不進去（%s）" % (sent_path(machine, dry), e.strerror or e))


def _split_key(key):
    """(schedule file, item name) of a state key. Either may hold a "#": the split that names a file that is
    there is taken, else the last one."""
    cuts = [i for i, c in enumerate(key) if c == "#"]
    for i in cuts:
        if os.path.isfile(key[:i]):
            return key[:i], key[i + 1:]
    return (key[:cuts[-1]], key[cuts[-1] + 1:]) if cuts else (key, "")


def stuck(machine, older_than, now=None, dry=False, tz=None):
    """Items claimed more than `older_than` seconds ago whose run never said how it went (its process was
    killed): [{"key", "path", "item", "slot", "text", "at", "at_text", "attempts"}], oldest first. text and
    at_text are the scheduled time and the time of the claim in the instance's zone (tz, else timezone(),
    else the machine's). They are never served again by themselves; this is for doctor to show.
    StateUnreadable as read_state()."""
    now = int(time.time()) if now is None else int(now)
    if tz is None:
        tz = timezone()[0] or SystemZone()
    out = []
    for key, entry in read_state(machine, dry).items():
        if entry["state"] == "claimed" and now - entry.get("at", 0) > older_than:
            path, name = _split_key(key)
            out.append({"key": key, "path": path, "item": name, "slot": entry["slot"], "text": tz.iso(entry["slot"]),
                        "at": entry.get("at"), "at_text": tz.iso(entry["at"]) if "at" in entry else None,
                        "attempts": entry.get("attempts", 1)})
    return sorted(out, key=lambda d: (d["at"] or 0, d["key"]))


def files_path(machine, dry=False):
    return _machine_file("files-dry" if dry else "files", machine, ".json")


def note_files(machine, good, bad, now=None, dry=False, labels=None):
    """Keep count, file by file, of rounds in a row a schedule file could not be read.
    good: {path: its plugin's name or None} for files read this round; bad: {path: why}. labels: {path: a
    plugin's name} from the caller, which wins over what the file last said. Returns what to tell the owner:
    [text], one for each file at its NOTIFY_AFTER-th round and once a day after that for as long as it
    goes on. A file read again starts over; a file no longer handed in is dropped. The counts are in a
    small file of their own, read, changed and written under a lock: of several processes in the same
    round, one counts a file and one is given the message. Nothing is written while nothing changes."""
    at = int(time.time()) if now is None else int(now)
    path = files_path(machine, dry)
    tell = []
    try:
        with _locked(path):
            old = config.read_json(path)
            old = old if isinstance(old, dict) else {}
            new = {}
            for p, name in good.items():
                new[os.path.realpath(os.path.expanduser(p))] = {"plugin": (labels or {}).get(p) or name}
            for p, why in bad.items():
                real = os.path.realpath(os.path.expanduser(p))
                was = old.get(real) if isinstance(old.get(real), dict) else {}
                if _same_round(was.get("last"), at) and isinstance(was.get("count"), int):
                    new[real] = was  # another process of this round counted it, and tells if it is to be told
                    continue
                count = was["count"] + 1 if isinstance(was.get("count"), int) else 1
                told = was.get("told") if isinstance(was.get("told"), int) else None
                plugin_name = (labels or {}).get(p) or (was.get("plugin") if isinstance(was.get("plugin"), str) else None)
                mine = count >= NOTIFY_AFTER and (told is None or at - told >= NOTIFY_AGAIN or at < told)
                if mine:
                    told = at
                    tell.append("排程器連續 %d 輪讀不懂%s排程檔 %s，裡面的項目都停著：%s"
                                % (count, "插件「%s」的" % plugin_name if plugin_name else "", p, why))
                new[real] = {"plugin": plugin_name, "count": count, "since": was.get("since", at), "last": at,
                             "told": told, "why": why}
            if new != old:
                tmp = "%s.%d.tmp" % (path, os.getpid())
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(new, f, ensure_ascii=False, indent=1, sort_keys=True)
                os.replace(tmp, path)
    except OSError:
        return []  # not counted, not told: a message that cannot be noted as sent would be sent every round
    return tell


@contextlib.contextmanager
def _locked(path):
    """Hold the lock that goes with a small file of counts while it is read, changed and written."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path + ".lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def _same_round(a, b):
    """Two moments of the same round (rounds are a minute apart): several processes of one round count once."""
    return isinstance(a, int) and isinstance(b, int) and a // 60 == b // 60


def note_bad_round(machine, why, now=None, dry=False, kind="state"):
    """Count one more round in a row that went wrong (kind "state" or "zone"). Returns (count, tell): tell is
    True at the NOTIFY_AFTER-th round and once a day after that, for as long as it goes on. Read, counted
    and written under a lock: of several processes in the same round, one counts it and one is told to
    tell. The count is kept in a small file of its own and starts over when that file cannot be read
    either; when it cannot be locked or written, nobody is told (count as far as it was known)."""
    path = unreadable_path(machine, dry, kind)
    at = int(time.time()) if now is None else int(now)
    try:
        with _locked(path):
            data = config.read_json(path)
            if not isinstance(data, dict) or not isinstance(data.get("count"), int):
                data = {}
            if _same_round(data.get("last"), at):
                return data["count"], False  # this round is counted already, and told if it was to be
            count = data.get("count", 0) + 1
            told = data.get("told") if isinstance(data.get("told"), int) else None
            tell = count >= NOTIFY_AFTER and (told is None or at - told >= NOTIFY_AGAIN or at < told)
            tmp = "%s.%d.tmp" % (path, os.getpid())
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"count": count, "since": data.get("since", at), "last": at, "told": at if tell else told,
                           "why": why}, f, ensure_ascii=False)
            os.replace(tmp, path)
            return count, tell
    except OSError:
        data = config.read_json(path)
        return (data["count"] if isinstance(data, dict) and isinstance(data.get("count"), int) else 0) + 1, False


def clear_bad_rounds(machine, dry=False, kind="state"):
    try:
        os.unlink(unreadable_path(machine, dry, kind))
    except OSError:
        pass


def set_aside(machine, now=None, dry=False):
    """Move a state file that cannot be read out of the way, its name carrying the time, so that a new one can
    be started. Under the lock, and only if it still cannot be read. Returns the new path, or None when the
    state reads again (someone repaired it). OSError when it cannot be moved."""
    path = sent_path(machine, dry)
    now = int(time.time()) if now is None else int(now)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path + ".lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            _read_entries(path)
            return None
        except StateUnreadable:
            pass
        aside = "%s.unreadable-%s" % (path, time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(now)))
        n = 1
        while os.path.lexists(aside):
            n += 1
            aside = "%s.unreadable-%s-%d" % (path, time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(now)), n)
        os.rename(path, aside)
        return aside


def beside_default():
    """/tmp/altention-<uid>-<the first 8 hex digits of the SHA-256 of the home's real path>: a folder of this
    user's and of this home's. By the uid alone it was shared by every home of the user: a rehearsal in a
    throwaway home that forgot ALT_RUNTIME wrote its mark, with a time of its own, over the real round's
    (2026-10-08). The home is asked at call time, as every path here is."""
    home = os.path.realpath(os.path.expanduser("~"))
    return "/tmp/altention-%d-%s" % (os.getuid(), hashlib.sha256(os.fsencode(home)).hexdigest()[:8])


def beside_dir():
    """A place outside the home for the mark of the last good round: the state folder may be the very thing
    that is broken (a home on a file server). $ALT_RUNTIME, else beside_default(). None when it is not this
    user's own folder (someone else made it first). Whoever writes the mark and whoever reads it (doctor, on
    that machine) come here, so both follow the one rule; a mark left under the name of before 2026-10-09
    (/tmp/altention-<uid>) is not looked for, it goes with the machine's next restart."""
    path = os.environ.get("ALT_RUNTIME") or beside_default()
    try:
        os.makedirs(path, mode=0o700, exist_ok=True)
        st = os.lstat(path)
    except OSError:
        return None
    return path if st.st_uid == os.getuid() and not os.path.islink(path) and os.path.isdir(path) else None


def round_paths(machine, dry=False):
    name = "%s.%s.json" % ("round-dry" if dry else "round", re.sub(r"[/\\\0]", "_", machine))
    beside = beside_dir()
    return [os.path.join(state_dir(), name)] + ([os.path.join(beside, "scheduler-" + name)] if beside else [])


def round_done(machine, now=None, dry=False):
    """Say that a round went through to its end (whoever runs the items calls this last; a round that was
    skipped or could not write does not). Written in the state folder and beside it (beside_dir), so the
    watchman can read it when either is out of reach. Returns how many of the two were written."""
    at = int(time.time()) if now is None else int(now)
    written = 0
    for path in round_paths(machine, dry):
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            tmp = "%s.%d.tmp" % (path, os.getpid())
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"machine": machine, "at": at, "text": _text(at, None)}, f)
            os.replace(tmp, path)
            written += 1
        except OSError:
            continue
    return written


MARK_SLACK = 120  # seconds: a round mark later than now by more than this is not believed


def last_good_round(machine, dry=False, now=None, future=None):
    """When the last round went through to its end (epoch seconds): the later of the two marks round_done()
    leaves; None when neither can be read. The one beside the home goes away when the machine restarts. Each mark
    later than now (beyond MARK_SLACK) is left out first, on its own, before the later one is taken: the one beside
    the home is shared by every home of this user (a test, a throwaway home with a clock ahead: #code-review-2
    2026-10-07). `future`, a list, gets the marks left out."""
    now = time.time() if now is None else now
    best = None
    for path in round_paths(machine, dry):
        data = config.read_json(path)
        at = data.get("at") if isinstance(data, dict) else None
        if not isinstance(at, int) or isinstance(at, bool):
            continue
        if at > now + MARK_SLACK:
            if future is not None:
                future.append(at)
            continue
        if best is None or at > best:
            best = at
    return best


def record(machine, entries, limit=None, kind="would-run"):
    """Append lines to a log; beyond `limit` bytes the log is first renamed to .1 (the older .1 goes away)."""
    if not entries:
        return
    path = log_path(machine, kind)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        try:
            if os.path.getsize(path) > (LOG_MAX if limit is None else limit):
                os.replace(path, path + ".1")
        except OSError:
            pass
        with open(path, "a", encoding="utf-8") as f:
            f.write("".join(json.dumps(e, ensure_ascii=False, sort_keys=True) + "\n" for e in entries))
    except OSError:
        pass  # a log that cannot be written never stops a round


def start_round(paths, machine, now=None, tz=None, host=None, dry=False, labels=None):
    """What one round is to serve, after noting where newly seen items start from. Runs nothing.
    Returns {"due", "fresh", "held", "gone", "not_mine", "problems", "skipped", "notify", "zone"}:
    - not_mine: items marked "scheduler": false: they stay in a cron line of their own, never scheduled here.
    - due: as plan() gives it. Claim each before serving it.
    - fresh: items seen for the first time (or switched off): noted, not served (but see ONCE_GRACE).
    - gone: notes of items no longer in their schedule file (the file itself read well): dropped, so an
      item that comes back is new. Items of a file that cannot be read, or that was not handed in, are kept:
      forget() drops those, when the caller knows the file's plugin is switched off.
    - held: items left alone this round because the zone is not known ("at", "once"); the rest goes on.
    - problems: [(what, why)].
    - skipped: None, or "state": the whole round does nothing, because the state file is there but cannot
      be read, or the starting points could not be noted. Nothing was written to the state then. After
      NOTIFY_AFTER such rounds in a row the unreadable file is set aside (kept, its name carrying the time)
      and the state starts anew: every item is seen for the first time, so nothing is served twice.
    - notify: messages to send to the owner ([] mostly): the zone unknown, the state unreadable, or one
      schedule file unreadable (which file of which plugin) for NOTIFY_AFTER rounds in a row, and once a
      day after that for as long as it goes on.
    - zone: the zone used, None when it is not known.
    paths: schedule files and plugin.json files. labels: {path: plugin name}, for naming a file in a message
    when the file itself cannot say (optional: a plugin.json's own name is remembered from when it read)."""
    now = int(time.time()) if now is None else int(now)
    out = {"due": [], "fresh": [], "held": [], "gone": [], "not_mine": [], "problems": [], "skipped": None,
           "notify": [], "zone": tz}
    kind = "would-run" if dry else "problems"
    lines = []

    def close():
        stamp = _text(now, out["zone"] or SystemZone())
        record(machine, [dict(line, at=stamp) for line in lines], kind=kind)
        return out

    if tz is None:
        tz, _, problem = timezone()
        out["zone"] = tz
        if tz is None:
            count, tell = note_bad_round(machine, problem, now, dry, "zone")
            text = "%s；需要時區的項目先不做（連續第 %d 輪）" % (problem, count)
            out["problems"].append(("core.timezone", text))
            lines.append({"file": "core.timezone", "problem": text})
            if tell:
                out["notify"].append("排程器連續 %d 輪拿不到時區，照時刻跑的項目都停著：%s" % (count, problem))
        else:
            clear_bad_rounds(machine, dry, "zone")
    loaded = _load_all(paths, host)
    out["notify"] += note_files(machine, {p: sched.get("plugin") for p, sched in loaded[0]}, dict(loaded[1]), now, dry, labels)
    try:
        try:
            state = read_state(machine, dry)
        except StateUnreadable as e:
            count, tell = note_bad_round(machine, str(e), now, dry, "state")
            if count < NOTIFY_AFTER:
                raise StateUnreadable("%s；這一輪不做也不寫（連續第 %d 輪）" % (e, count))
            try:
                aside = set_aside(machine, now, dry)
            except OSError as moved:
                if tell:
                    out["notify"].append("排程器的狀態連續 %d 輪讀不出來，也移不開，這台的排程都停著：%s" % (count, e))
                raise StateUnreadable("%s；移不開（%s），這一輪不做也不寫（連續第 %d 輪）" % (e, moved, count))
            state = read_state(machine, dry)
            if aside:
                text = "狀態連續 %d 輪讀不出來，已改名留著：%s；重新開始，每一項都當成第一次見到（這一輪不發）" % (count, aside)
                out["problems"].append((sent_path(machine, dry), text))
                out["notify"].append("排程器的%s（%s）" % (text, e))
        j = _judge(paths, state, now, tz, host, loaded)
        out["problems"] += [p for p in j["problems"] if p[0] != "core.timezone"]
        out["fresh"], out["held"], out["gone"], out["not_mine"] = j["fresh"], j["held"], j["gone"], j["not_mine"]
        if out["fresh"] or out["gone"]:
            mark_seen(machine, out["fresh"], now, tz, dry, out["gone"])
        out["due"] = j["due"]
    except (StateUnreadable, OSError) as e:
        out.update(skipped="state", due=[], fresh=[], held=[], gone=[], not_mine=[])
        out["problems"] = [p for p in out["problems"] if p[0] == "core.timezone"] + list(loaded[1])
        out["problems"].insert(0, (sent_path(machine, dry), str(e)))
        lines.append({"file": sent_path(machine, dry), "problem": str(e), "skipped": "state"})
        return close()
    clear_bad_rounds(machine, dry, "state")
    lines += [{"file": what, "problem": why} for what, why in out["problems"] if what != "core.timezone"]
    return close()


def record_only(paths, machine, now=None, tz=None, host=None, labels=None):
    """One round of the record-only mode: write down what would be served this minute, serve nothing.
    Returns (due, problems). It follows the same rules as a real round (first sight, unreadable state, zone
    not known, claiming), with notes of its own (dry.<machine>.json) apart from the real ones."""
    now = int(time.time()) if now is None else int(now)
    r = start_round(paths, machine, now, tz, host, dry=True, labels=labels)
    if r["skipped"]:
        return [], r["problems"]
    zone = r["zone"] or SystemZone()  # for the times in the log only
    found, problems, whole = [], r["problems"], True
    for d in r["due"]:
        try:
            if claim(machine, d["key"], d["slot"], now, zone, dry=True, mark=d["item"]["mark"], retry=d["retry"]):
                finish(machine, d["key"], d["slot"], True, now=now, dry=True)
                found.append(d)
        except (StateUnreadable, StateUnwritable) as e:
            problems.append((sent_path(machine, True), "「%s」沒有領到，不做：%s" % (d["item"]["name"], e)))
            whole = False
    record(machine, [{"at": zone.iso(now), "file": d["path"], "item": d["item"]["name"], "slot": zone.iso(d["slot"]),
                      "do": "message" if "message" in d["item"]["do"] else "run", "late": now - d["slot"],
                      "log": d["item"]["do"]["log"], "trace": d["item"]["do"].get("trace")} for d in found]
           + ([] if whole else [{"at": zone.iso(now), "file": what, "problem": why} for what, why in problems[-1:]]))
    if whole:
        round_done(machine, now, dry=True)
    return found, problems


def switch_preview(paths, machine, now, tz, host=None):
    """Before the record-only notes become the real ones: the items that would be taken for missed and made
    up for within PREVIEW seconds of `now`: those whose scheduled time is before `now` and that neither the
    record-only notes nor the real ones (where they are at least as late) count as served.
    [{"key", "path", "item", "slot"}]. Writes nothing. Right after adopt_dry() at the same `now` it is empty:
    that is how a switch is checked. StateUnreadable when either state cannot be read."""
    state = read_state(machine, dry=True)
    for key, entry in read_state(machine).items():  # a real note at least as late is the one that counts
        if key not in state or entry["slot"] >= state[key]["slot"]:
            state[key] = entry
    schedules, _ = _load_all(paths, host)
    seen, out = set(), []
    for t in range(int(now), int(now) + PREVIEW + 1, 60):
        for d in _plan(schedules, state, t, tz)["due"]:
            if d["slot"] < now and d["key"] not in seen:
                seen.add(d["key"])
                out.append({"key": d["key"], "path": d["path"], "item": d["item"], "slot": d["slot"]})
    return out


def adopt_dry(machine, now, tz=None):
    """Make the record-only notes the real ones, every item counted as done up to `now`: nothing scheduled
    before the switch is made up for afterwards (that covers everything switch_preview() lists). A real
    note that is later stays. Returns how many items were taken over. StateUnreadable when either file
    cannot be read; nothing is written then."""
    dry = read_state(machine, dry=True)

    def change(entries):
        taken = 0
        for key, old in dry.items():
            if key in entries and entries[key]["slot"] >= now:
                continue
            entry = {"slot": int(now), "state": "off" if old["state"] == "off" else "done", "at": int(now),
                     "text": _text(now, tz), "adopted": True}
            if old.get("when") is not None:
                entry["when"] = old["when"]
            entries[key] = entry
            taken += 1
        return taken, bool(taken)
    return _update(machine, False, change)


# ---------------------------------------------------------------------------------------------------- CLI
def check(paths, out=None):
    """--check: say what each file schedules, or why it cannot be read. Returns 0, or 1 if a file is wrong."""
    out = out or sys.stdout
    bad = 0
    for path in paths:
        try:
            schedule = load(path)
        except ScheduleError as e:
            out.write("%s：讀不懂（%s）\n" % (path, e))
            bad = 1
            continue
        if not schedule["items"]:
            out.write("%s：沒有任何項目\n" % path)
        for item in schedule["items"]:
            out.write("%s · %s\n" % (path, describe(item)))
    return bad


def main(argv):
    if len(argv) >= 2 and argv[0] == "--check":
        return check(argv[1:])
    sys.stderr.write("用法：python3 -m altention.scheduler --check 排程檔…\n")
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
