"""The mark round a quote handed to a Claude (bot/util.mark(), C-172a P1c) made the same in tests, and taken out
again for the tests about what else the text holds: what the marks do is tested where they are made
(tests/test_reserved_frames.py)."""
import re
from unittest import mock

from bot import util

MARK = "m0"
_LINE = re.compile(r"<<<原文 \w+ (開始|結束)>>>\n")
_LAST = re.compile(r"\n<<<原文 \w+ 結束>>>$")


def fixed(case):
    """util.mark() gives MARK for the rest of the test `case`."""
    p = mock.patch.object(util, "mark", lambda: MARK)
    p.start()
    case.addCleanup(p.stop)


def plain(text):
    """`text` without the two lines of a mark round each quote (MARK or any other)."""
    return _LAST.sub("", _LINE.sub("", text)) if isinstance(text, str) else text
