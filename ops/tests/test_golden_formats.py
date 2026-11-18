"""C-148 batch 2: golden files of the files the engine writes that another program, version or machine reads (spec
§12.5.6, §14.3, §16.1): a new instance's plugins.json, a hook as apply registers it in ~/.claude/settings.json, and
crontab lines (each "when" as cron reads it, the marker, % escaped). A change here is a change of interface:
tools/golden update <case>, and a "golden:" line in the commit."""
import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))))
from altention import testing as _testing  # noqa: E402
_testing.guard_home()  # tests never touch the real home (altention/testing.py)
import os
import sys
import unittest
from unittest import mock

from altention import testing

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import applycore  # noqa: E402
import instance  # noqa: E402
import plancore  # noqa: E402


class Instance(unittest.TestCase):
    def test_a_new_instances_plugins_json(self):
        """§12.5.6: as seed and init write it (the time zone this machine's, made fixed here)."""
        with mock.patch.object(instance, "system_timezone", return_value="Asia/Taipei"):
            got = {"seed": instance.plugins_skeleton(), "init (install.sh)": instance.plugins_skeleton(permission="auto")}
        testing.golden("formats/new-instance-plugins", got, "§12.5.6")


class Hooks(unittest.TestCase):
    def test_a_hook_as_registered(self):
        """§14.3: each hook apply adds is a group of its own in settings.json, its matcher and timeout as declared."""
        def want(event, matcher, command, timeout):
            return {"plugin": "demo", "name": event, "kind": "hook", "text": command,
                    "key": (event, matcher, tuple(plancore.words(command)), timeout)}
        before = {"permissions": {"allow": []}, "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "theirs.sh"}]}]}}
        result = plancore.compare([want("Stop", "", "/e/dcbot/tools/dchook --global", 20),
                                   want("PreToolUse", "AskUserQuestion", "/e/dcbot/tools/dchook --ask", 43200),
                                   want("SessionStart", "", "/e/x/hook.py", None)], [], [])
        new, _, _ = applycore.new_settings(before, result)
        testing.golden("formats/hook-registration", new, "§14.3")


class Cron(unittest.TestCase):
    def test_when_as_cron_reads_it(self):
        """§16.1, §16.3: every "when" as its five fields; the machine's offset from its name (md5 % 60 % every)."""
        whens = [{"every": "10m"}, {"every": "30m", "offset": "7m"}, {"every": "1h"}, {"every": "6h"}, {"every": "2h", "offset": "90m"},
                 {"every": "15m", "offset": "machine"}, {"every": "1h", "offset": "machine"}, {"every": "15m", "offset": "machine", "after": "5m"},
                 {"every": "45m"}, {"every": "10m", "offset": "10m"}, {"at": "03:00", "days": "daily"},
                 {"at": "21:30", "days": ["sun"]}, {"at": "07:05", "days": ["mon", "fri"]}, "@reboot", "*/5 * * * *"]
        rows = []
        for w in whens:
            try:
                rows.append("%s → %s\n" % (w, plancore.cron_time(w, "host-a")))
            except ValueError as e:
                rows.append("%s → ValueError %s\n" % (w, e))
        testing.golden("formats/cron-when", "".join(rows), "§16.1")

    def test_the_line_and_its_marker(self):
        """§16.1: a schedule item's line: % escaped, output thrown away unless asked, and the marker at its end."""
        d = {"name": "demo", "channels": [{"name": "demo", "setting": "demo.channel"}]}
        items = [{"name": "run", "when": {"every": "10m"}, "do": {"run": "{python} \"{plugin}/x.py\" --at %H"}},
                 {"name": "logged", "when": {"at": "03:00"}, "do": {"run": "{plugin}/y.sh", "log": "{data}/y.log", "env": {"LANG": "C.UTF-8"}}},
                 {"name": "say", "when": {"at": "08:00"}, "do": {"message": "早安 100%", "channel": "demo"}},
                 {"name": "bad name!", "when": {"every": "1h"}, "do": {"run": "true", "output": "keep"}}]
        rows = []
        with mock.patch.dict(os.environ, {"ALT_DATA": "/d"}):
            for s in items:
                t, line = plancore.render_schedule("demo", "/p/demo", d, s, {"demo": {"channel": "123"}}, "host-a")
                rows.append("%s %s  # altention:%s\n" % (t, line, plancore.marker({"plugin": "demo", "name": s["name"]})))
        testing.golden("formats/cron-lines", "".join(rows), "§16.1")


if __name__ == "__main__":
    unittest.main()
