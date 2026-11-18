"""C-172a P1b: what the bot hands Discord stays the same, field by field, while its sends move into deliver()
(tests/payloadlog.py; the baselines in tests/samples/payloads/, each written before its batch moved anything)."""
import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))))
from altention import testing as _testing  # noqa: E402
_testing.guard_home()  # tests never touch the real home (altention/testing.py)
import contextlib
import io
import unittest

from tests import payloadlog
from tests import tmuxguard  # noqa: F401  (no test reaches a real tmux server)


class Same(unittest.TestCase):
    def test_p1b1(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = payloadlog.main(["--check", "p1b1"])
        self.assertEqual(code, 0, out.getvalue())

    def test_p1b2(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = payloadlog.main(["--check", "p1b2"])
        self.assertEqual(code, 0, out.getvalue())

    def test_p1b3(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = payloadlog.main(["--check", "p1b3"])
        self.assertEqual(code, 0, out.getvalue())

    def test_p1b3b(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = payloadlog.main(["--check", "p1b3b"])
        self.assertEqual(code, 0, out.getvalue())

    def test_p1c(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = payloadlog.main(["--check", "p1c"])
        self.assertEqual(code, 0, out.getvalue())


if __name__ == "__main__":
    unittest.main()
