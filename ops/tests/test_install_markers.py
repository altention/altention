"""No install script writes the crontab or Claude Code's settings.json: plugins.py apply alone does, from
plugin.json (the scripts' own lines and markers are gone from the public version)."""
import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))))
from altention import testing as _testing  # noqa: E402
_testing.guard_home()  # tests never touch the real home (altention/testing.py)
import glob
import os
import unittest

ENGINE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class Scripts(unittest.TestCase):
    def test_no_install_script_writes_cron_or_hooks(self):
        scripts = glob.glob(os.path.join(ENGINE, "*", "install.sh")) + [os.path.join(ENGINE, "dcbot", "uninstall.sh")]
        self.assertGreaterEqual(len(scripts), 4)
        for path in scripts:
            with open(path, encoding="utf-8") as f:
                text = f.read()
            with self.subTest(script=os.path.relpath(path, ENGINE)):
                self.assertNotIn("crontab", text)
                self.assertNotIn("settings.json", text)
                self.assertNotIn("dchook --", text)

    def test_apply_never_writes_the_crontab_itself(self):
        """ops/apply.py leaves the crontab and the hooks to plugins.py apply."""
        with open(os.path.join(ENGINE, "ops/apply.py"), encoding="utf-8") as f:
            text = f.read()
        self.assertNotRegex(text, r"\|\s*crontab -(\s|$)", "nothing piped into crontab (crontab -e in a message is fine)")


if __name__ == "__main__":
    unittest.main()
