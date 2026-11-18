"""C-148 batch 4: golden of how old crontab lines are told (spec §17.4, §16.1): the new marker, one without a
name, the old ones (# planner, # usage) at a line's end only, and other people's lines. A change here is a change of
what is taken as ours on someone's machine (tools/golden update <case>, and a "golden:" line in the commit)."""
import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))))
from altention import testing as _testing  # noqa: E402
_testing.guard_home()  # tests never touch the real home (altention/testing.py)
import os
import sys
import unittest

from altention import testing

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import plancore  # noqa: E402

ENGINE = "/e/altention/engine"


class OldCron(unittest.TestCase):
    def test_which_lines_are_ours(self):
        lines = ["*/10 * * * * /usr/bin/python3 /p/x.py  # altention:demo/run",
                 "0 * * * * true  # altention:demo",
                 "0 8 * * * /usr/bin/python3 %s/planner/remind.py  # planner" % ENGINE,
                 "5 * * * * /usr/bin/python3 %s/usage/collect.py # usage" % ENGINE,
                 "5 * * * * echo '# planner' > /tmp/x",
                 "0 3 * * * /usr/bin/python3 %s/altention/cron_update.py" % ENGINE,
                 "0 4 * * * backup.sh  # mine",
                 "SHELL=/bin/bash"]
        rows = ["%s → ours=%s marked=%s name=%s\n" % (l, plancore.ours(l, ENGINE), plancore.marked(l), plancore.marked_name(l))
                for l in lines]
        testing.golden("compat/cron-old-lines", "".join(rows), "§17.4")


if __name__ == "__main__":
    unittest.main()
