"""C-148 batch 2: golden files of what the bot and its hook write for others to read (spec §13.3, §13.8): the
fields a hook event carries beside Claude Code's own (another machine's hook may be another version), and
state/waiting-on-owner.json (doctor, the status board). A change here is a change of interface: tools/golden update
<case>, and a "golden:" line in the commit."""
import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))))
from altention import testing as _testing  # noqa: E402
_testing.guard_home()  # tests never touch the real home (altention/testing.py; 11-08: one left a real dcremote-owner)
import asyncio
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))  # the engine
from altention import testing  # noqa: E402
from tests import test_awaiting, test_hook_fields  # noqa: E402
from tests import tmuxguard  # noqa: F401,E402  (no test reaches a real tmux server)

HERE = os.path.dirname(os.path.abspath(__file__))


class HookFields(unittest.TestCase):
    def test_the_fields_a_hook_adds(self):
        """§13.8: the names of ours (dc_ and the old six), what is taken out, the relay's, and each event's added."""
        added = {}
        for name, payload in test_hook_fields.payloads():
            got = test_hook_fields.Fields.through_the_hook(self, payload)  # its helper alone, not its tests
            added.setdefault(payload["hook_event_name"], set()).update(k for k in got if k not in payload)
        got = {"prefix": test_hook_fields.dchook.OURS_PREFIX, "before": sorted(test_hook_fields.dchook.OURS_BEFORE),
               "taken_out": sorted(test_hook_fields.dchook.TAKEN_OUT), "relay_adds": sorted(test_hook_fields.dcremote.ADDED),
               "added_by_event": {k: sorted(v) for k, v in sorted(added.items())}}
        testing.golden("bot-state/hook-fields", got, "§13.8", folder=HERE)


class Waiting(test_awaiting.Base):
    def test_waiting_on_owner(self):
        """§13.3: {"at", "waiting": [{"channel", "kind", "since", "text"?}]}."""
        async def go():
            await self.asker.post(self.ch, {"ask_id": "7-1", "questions": [test_awaiting.Q1]})
            await self.asker.post_plan(self.ch, {"ask_id": "7-2", "plan": "do it"})
            return self.whole()
        whole = asyncio.run(go())
        testing.golden("bot-state/waiting-on-owner", testing.shape(whole, keep=("kind",)), "§13.3", folder=HERE)


if __name__ == "__main__":
    unittest.main()
