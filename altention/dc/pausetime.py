"""The times of a channel's pause (C-184): `altention dc post <channel> --pause-until <time>` reads them here, and the
bot and the status board show them the same way. Wall-clock times are the instance's (core.timezone, as the
schedules go by: altention/scheduler.py); when that cannot be told, this machine's own zone."""
import datetime
import re
import time

MOST = 7 * 86400  # a pause longer than a week is refused: nobody remembers why by then
RELATIVE = re.compile(r"^(\d{1,4})\s*([mhd])$")
CLOCK = re.compile(r"^(\d{1,2}):(\d{2})$")
DATED = re.compile(r"^(\d{4})-(\d{1,2})-(\d{1,2})[ T](\d{1,2}):(\d{2})$")
UNIT = {"m": 60, "h": 3600, "d": 86400}


class Local(object):
    """This machine's zone, with the two questions the scheduler's zones answer (when the instance's is not known)."""
    def local(self, ts):
        return datetime.datetime.fromtimestamp(int(ts))

    def resolve(self, naive):
        return int(time.mktime(naive.timetuple()))


def zone():
    """The instance's zone (core.timezone), else this machine's."""
    try:
        from altention import scheduler
        z = scheduler.timezone()[0]
    except Exception:
        z = None
    return z if z is not None else Local()


def parse(text, now=None, tz=None):
    """(the instant, None) a --pause-until time names, or (None, why not). HH:MM is the next time the clock says it
    (00:00 is the coming midnight); YYYY-MM-DD HH:MM; 30m, 2h, 1d from now. One already past, or more than MOST
    ahead, is refused."""
    now = time.time() if now is None else now
    tz = zone() if tz is None else tz
    t = " ".join((text or "").split())
    m = RELATIVE.match(t)
    if m:
        until = now + int(m.group(1)) * UNIT[m.group(2)]
    else:
        m, d = CLOCK.match(t), DATED.match(t)
        try:
            if m:
                h, mi = int(m.group(1)), int(m.group(2))
                today = tz.local(now).replace(hour=h, minute=mi, second=0, microsecond=0)
                until = tz.resolve(today)
                if until <= now:
                    until = tz.resolve(today + datetime.timedelta(days=1))
            elif d:
                until = tz.resolve(datetime.datetime(*[int(x) for x in d.groups()]))
            else:
                return None, "看不懂時間「%s」：要寫 HH:MM、YYYY-MM-DD HH:MM，或 30m、2h、1d" % text
        except ValueError:
            return None, "看不懂時間「%s」：沒有這個時刻" % text
    if until <= now:
        return None, "%s 已經過了" % shown(until, now, tz)
    if until - now > MOST:
        return None, "最多暫停 7 天（%s 太遠了）" % shown(until, now, tz)
    return int(until), None


def shown(ts, now=None, tz=None):
    """An instant as the owner reads it: HH:MM today, MM-DD HH:MM another day."""
    now = time.time() if now is None else now
    tz = zone() if tz is None else tz
    at, today = tz.local(ts), tz.local(now)
    return at.strftime("%H:%M") if at.date() == today.date() else at.strftime("%m-%d %H:%M")
