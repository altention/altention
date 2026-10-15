"""The mark of a background Claude (altention/background.py)."""
import os
import subprocess
import sys
import unittest
from unittest import mock

from altention import background


class Mark(unittest.TestCase):
    def test_the_environment_given_carries_it_and_the_callers_own_does_not(self):
        with mock.patch.dict(os.environ, {"KEPT": "1"}):
            os.environ.pop(background.NAME, None)
            env = background.env()
            self.assertEqual((env[background.NAME], env["KEPT"]), ("1", "1"))
            self.assertNotIn(background.NAME, os.environ)
            self.assertFalse(background.marked())
        base = {"A": "b"}
        self.assertEqual(background.env(base), {"A": "b", background.NAME: "1"})
        self.assertEqual(base, {"A": "b"}, "not changed in place")
        self.assertEqual(background.env({}), {background.NAME: "1"}, "an empty one is an empty one, not this process's")

    def test_marked(self):
        self.assertTrue(background.marked(background.env({})))
        self.assertFalse(background.marked({}))
        self.assertFalse(background.marked({background.NAME: ""}), "set to nothing: not marked")
        self.assertFalse(background.marked({"CHATLOG_SKIP": "1"}), "the name one part had of its own until stage 3b step 1.9b")
        with mock.patch.dict(os.environ, {background.NAME: "1"}):
            self.assertTrue(background.marked())

    def test_what_a_background_claude_starts_is_marked_too(self):
        r = subprocess.run([sys.executable, "-c", "import os; print(os.environ.get('%s'))" % background.NAME],
                           env=background.env(), stdout=subprocess.PIPE, universal_newlines=True)
        self.assertEqual(r.stdout.strip(), "1")

    def test_the_name(self):
        self.assertEqual(background.NAME, "ALTENTION_BACKGROUND")


if __name__ == "__main__":
    unittest.main()
