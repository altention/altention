"""C-162 drill (bot/cc_drill.py): its small pure parts (the drill itself runs a real Claude Code: by hand)."""
import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))))
from altention import testing as _testing  # noqa: E402
_testing.guard_home()  # tests never touch the real home (altention/testing.py; 11-08: one left a real dcremote-owner)
import os
import subprocess
import tempfile
import unittest
from unittest import mock

from bot import cc_drill
from tests import tmuxguard  # noqa: F401  (no test reaches a real tmux server)


class OlderVersion(unittest.TestCase):
    def test_by_number_and_older_only(self):
        folder = tempfile.mkdtemp()
        for name in ("2.1.99", "2.1.100", "2.1.101", "2.1.102", "notes.txt", "2.1.x"):
            open(os.path.join(folder, name), "w").close()
        self.assertEqual(cc_drill.older_version(os.path.join(folder, "2.1.101")), os.path.join(folder, "2.1.100"))
        self.assertEqual(cc_drill.older_version(os.path.join(folder, "2.1.100")), os.path.join(folder, "2.1.99"))
        self.assertIsNone(cc_drill.older_version(os.path.join(folder, "2.1.99")), "none older")
        self.assertIsNone(cc_drill.older_version(os.path.join(folder, "claude")), "a name that is no version")


class Leftover(unittest.TestCase):
    def setUp(self):
        self.state = tempfile.mkdtemp()
        p = mock.patch.object(cc_drill, "last_path", lambda: os.path.join(self.state, "cc-drill-last.json"))
        p.start()
        self.addCleanup(p.stop)
        self.ran = []
        p = mock.patch.object(cc_drill.subprocess, "run", lambda cmd, **kw: self.ran.append(cmd))
        p.start()
        self.addCleanup(p.stop)

    def world(self, pid):
        root = tempfile.mkdtemp(prefix="ccd.")
        with mock.patch.object(cc_drill.os, "getpid", lambda: pid):
            cc_drill.record(root, ("dcbot-test-drill-%d" % pid, "dcbot-test-drill10-%d" % pid))
        return root

    def dead_pid(self):
        p = subprocess.Popen(["true"])
        p.wait()
        return p.pid

    def test_a_dead_drills_world_is_cleaned(self):
        pid = self.dead_pid()
        root = self.world(pid)
        self.assertIsNone(cc_drill.leftover())
        self.assertFalse(os.path.exists(root))
        self.assertEqual([c[2] for c in self.ran], ["dcbot-test-drill-%d" % pid, "dcbot-test-drill10-%d" % pid])
        self.assertFalse(os.path.exists(cc_drill.last_path()))

    def test_a_running_drill_is_left_alone(self):
        root = self.world(os.getppid())  # (a process that is alive)
        self.assertEqual(cc_drill.leftover(), "running")
        self.assertTrue(os.path.isdir(root))
        self.assertEqual(self.ran, [])
        os.rmdir(root)

    def test_an_old_record_is_no_drill_running(self):
        """A pid alive is not enough (after a reboot the number may be another program's): too old a record is gone."""
        root = self.world(os.getppid())
        with open(cc_drill.last_path()) as f:
            got = cc_drill.json.load(f)
        got["at"] -= cc_drill.GONE_AFTER + 1
        with open(cc_drill.last_path(), "w") as f:
            cc_drill.json.dump(got, f)
        self.assertIsNone(cc_drill.leftover())
        self.assertFalse(os.path.exists(root), "cleaned")

    def test_a_record_whose_folder_is_gone_is_no_drill_running(self):
        """After a reboot /tmp is empty: the folder gone, the drill is too, whatever runs under that pid now."""
        root = self.world(os.getppid())
        os.rmdir(root)
        self.assertIsNone(cc_drill.leftover())
        self.assertFalse(os.path.exists(cc_drill.last_path()))

    def test_only_its_own_kind_of_folder(self):
        elsewhere = tempfile.mkdtemp(dir=self.state)  # not a ccd. folder in the temp folder
        with mock.patch.object(cc_drill.os, "getpid", lambda: self.dead_pid()):
            cc_drill.record(elsewhere, ("tmux-of-someone",))
        cc_drill.leftover()
        self.assertTrue(os.path.isdir(elsewhere))
        self.assertEqual(self.ran, [])

    def test_nothing_recorded(self):
        self.assertIsNone(cc_drill.leftover())


class Summary(unittest.TestCase):
    def test_the_json_line(self):
        self.assertEqual(cc_drill.summary([True] * 11, True, True, "9.9.9"),
                         {"ok": True, "started": True, "screen": True, "print": True, "version": "9.9.9", "failed": [], "why": None})
        got = cc_drill.summary([True, True, True, True, False, True, True, True, True, True, False], True, True, "9.9.9")
        self.assertEqual((got["ok"], got["failed"], got["why"]), (False, [4, 10], "沒接住的步驟：4、10"))
        self.assertEqual(cc_drill.summary([], None, None, "9.9.9")["started"], False, "step 0 never ran")


if __name__ == "__main__":
    unittest.main()
