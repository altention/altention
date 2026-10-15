"""planner/remind.py goes by the instance's time zone (core.timezone, as the schedules), not a fixed one."""
import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))))
from altention import testing as _testing  # noqa: E402
_testing.guard_home()  # tests never touch the real home (altention/testing.py)
import calendar
import importlib.util
import io
import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

ENGINE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class FixedZone:
    def __init__(self, hours):
        self.hours = hours

    def local(self, ts):
        import datetime
        return datetime.datetime(1970, 1, 1) + datetime.timedelta(seconds=int(ts) + self.hours * 3600)


class Zone(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="提醒 ")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        spec = importlib.util.spec_from_file_location("remind_t", os.path.join(ENGINE, "planner", "remind.py"))
        self.remind = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.remind)
        self.remind.STATE = os.path.join(self.tmp, "state.json")
        self.file = os.path.join(self.tmp, "schedule.json")
        with open(self.file, "w", encoding="utf-8") as f:
            json.dump({"channel": "100000000000000001", "items": [{"name": "n", "at": "09:00", "text": "早安"}]}, f)

    def run_at(self, utc, zone):
        sent = []
        from altention import scheduler
        with mock.patch.object(scheduler, "timezone", return_value=zone), \
                mock.patch.object(self.remind.time, "time", return_value=calendar.timegm(utc)), \
                mock.patch.object(self.remind.subprocess, "run", lambda argv: sent.append(argv[2:]) or mock.Mock(returncode=0)), \
                mock.patch("sys.stderr", new_callable=io.StringIO) as err:
            self.remind.main([self.file])
        os.remove(self.remind.STATE)
        return sent, err.getvalue()

    def test_the_hour_is_the_instances(self):
        nine_in_london = (2026, 1, 5, 9, 1, 0)  # 09:01 UTC; 17:01 in Taipei
        sent, _ = self.run_at(nine_in_london, (FixedZone(0), "setting", None))
        self.assertEqual(sent, [["post", "100000000000000001", "早安"]])
        sent, _ = self.run_at(nine_in_london, (FixedZone(8), "setting", None))
        self.assertEqual(sent, [], "17:01 there: past 09:00 by more than three hours")

    def test_a_setting_that_cannot_be_used_falls_back_to_the_machine_and_says_so(self):
        from altention import scheduler
        with mock.patch.object(scheduler, "SystemZone", lambda: FixedZone(0)):
            sent, err = self.run_at((2026, 1, 5, 9, 1, 0), (None, "setting", "時區名稱不對"))
        self.assertEqual(len(sent), 1)
        self.assertIn("時區名稱不對", err)


if __name__ == "__main__":
    unittest.main()
