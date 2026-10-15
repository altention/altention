"""chatlog only exports conversations: the engine and the instance are kept in step by altention/cron_sync.py and
altention/cron_update.py, their own cron lines (3a.10). chatlog's cron never touches the engine's repo."""
import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))))
from altention import testing as _testing  # noqa: E402
_testing.guard_home()  # tests never touch the real home (altention/testing.py)
import importlib.util
import os
import unittest

PATH = os.path.join(os.path.dirname(__file__), "..", "chatlog.py")
spec = importlib.util.spec_from_file_location("chatlog", PATH)
chatlog = importlib.util.module_from_spec(spec)
spec.loader.exec_module(chatlog)


class NoEngineSync(unittest.TestCase):
    def test_no_sync_of_the_engine_left_in_chatlog(self):
        for name in ("home_sync", "lifeline", "_home_sync", "HOME_REPO"):
            self.assertFalse(hasattr(chatlog, name), name)
        with open(PATH) as f:
            text = f.read()
        self.assertNotIn("altention import sync", text)
        self.assertNotIn("agent-git.lock", text, "the engine's lock is not chatlog's business any more")


if __name__ == "__main__":
    unittest.main()
