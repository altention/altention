"""C-148 batch 4: golden files of old data the engine must still read (spec §17): what it makes of an old schedule
file, of the bot's old settings file, and of a plugin.json with retired fields. What is locked is the reading,
not the old format: a change here means someone's machine reads its old data differently (tools/golden update
<case>, and a "golden:" line in the commit)."""
import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))))
from altention import testing as _testing  # noqa: E402
_testing.guard_home()  # tests never touch the real home (altention/testing.py)
import os
import tempfile
import unittest

from altention import botmove, plugin, scheduler, testing


class OldSchedule(unittest.TestCase):
    def test_the_older_form_as_read(self):
        """§17.4: {"channel", "items": [{"name", "enabled", "at", "days", "text"}]} beside the when/do form."""
        data = {"channel": "secretary", "items": [
            {"name": "morning", "at": "08:00", "days": "daily", "text": "早報"},
            {"name": "weekly", "enabled": False, "at": "21:00", "days": ["sun"], "text": "回顧"},
            {"name": "new", "when": {"every": "30m"}, "do": {"message": "x"}}]}
        testing.golden("compat/schedule-old", scheduler.parse(data, host="host-a"), "§17.4")


class OldBotSettings(unittest.TestCase):
    def test_the_old_settings_file_as_read(self):
        """§17.3: KEY=VALUE, # comments, quoted values; which key goes where (secrets, settings). An `export ` line is
        read as a key of that name (manager 11-03: never written by the engine; botmove stops on it, the old file left)."""
        path = os.path.join(tempfile.mkdtemp(), "env")
        with open(path, "w") as f:
            f.write('# the bot\nDISCORD_TOKEN="x-token"\nOWNER_ID=42\n  GUILD_ID = \'7\'\nMAX_PARALLEL=3\nINTERIM=1\n'
                    'not a line\nEMPTY=\nCLAUDE_BIN=/usr/bin/claude # tail\nexport BRIDGE=tmux\n')
        got = {"read": botmove.read_old(path), "secrets": [list(s) for s in botmove.SECRETS],
               "settings": [[k, v, t.__name__] for k, v, t in botmove.SETTINGS]}
        testing.golden("compat/dcbot-env-old", got, "§17.3")


class RetiredFields(unittest.TestCase):
    def test_a_plugin_json_with_retired_fields(self):
        """§17.2, §10.6: commands[], requires[].data and a plugin's secrets[].path still taken; said as retired."""
        d = {"format": 1, "name": "demo", "title": "D", "description": "d", "machines": "all",
             "commands": [{"name": "x", "path": "x"}], "requires": [{"plugin": "rig", "data": 3}],
             "secrets": [{"name": "api-token", "path": "~/.config/altention/secrets/demo/api-token"}], "future": {"x": 1}}
        self.assertIs(plugin.check(d), d)
        testing.golden("compat/plugin-json-retired", [[w, todo] for w, todo in plugin.retired(d)], "§17.2")


if __name__ == "__main__":
    unittest.main()
