"""The time zone the usage boards go by: the instance's core.timezone, as the schedules do (altention/scheduler.py
timezone()), else this machine's. A setting that cannot be used, or the engine's shared code that does not load
here: this machine's, and why (for the cron log); remote.py still answers (#code-review on 8c64e13)."""
import datetime
import os
import sys
import time


class Here:
    """This machine's zone without the engine's code: what remote.py and collect.py use of a Zone."""
    name = "system"

    def offset_at(self, ts):
        return time.localtime(ts).tm_gmtoff

    def local(self, ts):
        return datetime.datetime.fromtimestamp(ts)


def zone():
    """(Zone, name, problem): Zone answers local(ts) and offset_at(ts); name is the setting's zone name for ccusage
    -z, or None when this machine's own is used (ccusage then takes it itself); problem is why a setting there was
    not used."""
    try:
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        from altention import config, scheduler
        z, source, problem = scheduler.timezone()
        if z is None:
            return Here(), None, problem
        return z, (config.setting("core.timezone") if source == "setting" else None), None
    except Exception as e:  # the engine's files beside this one cut short or missing: the boards go on
        return Here(), None, "讀不到引擎的時區設定（%r）" % e
