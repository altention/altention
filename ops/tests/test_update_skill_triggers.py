"""skills/update is what #setup's Claude uses when an update needs the person. Every sentence an update notice or
card tells them to say there (「到 #setup 跟 Claude 說「…」」 in altention/update.py and altention/update_go.py) is in
the skill's description, so saying it brings the skill up (manager 2026-10-20: three notices named a sentence the
description did not have). A new or reworded sentence in those files fails here until the skill has it.

The description is read as Claude Code reads it, as YAML: unquoted, " #" starts a comment and the rest is dropped
(#code-review-2 of 6b503b3: the update and setup skills reached the model cut at "handed to" and "in Discord's"),
so every SKILL.md in the engine is checked to read whole."""
import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))))
from altention import testing as _testing  # noqa: E402
_testing.guard_home()  # tests never touch the real home (altention/testing.py)

import glob
import json
import os
import re
import unittest

try:
    import yaml
except ImportError:  # a machine without PyYAML: the line itself is checked (quoted, or nothing YAML reads apart)
    yaml = None

ENGINE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SAY = re.compile(r"到 #setup 跟 Claude 說「([^」]+)」")


def read(*path):
    with open(os.path.join(ENGINE, *path), encoding="utf-8") as f:
        return f.read()


def skills():
    return sorted(glob.glob(os.path.join(ENGINE, "skills", "*", "SKILL.md"))
                  + glob.glob(os.path.join(ENGINE, ".claude", "skills", "*", "SKILL.md"))
                  + glob.glob(os.path.join(ENGINE, "*", "skills", "*", "SKILL.md")))


def description(path):
    """(the description as YAML gives it, the one the line means: unquoted, or its JSON-style quotes taken off)."""
    with open(path, encoding="utf-8") as f:
        head = f.read().split("---", 2)[1]
    line = next(l for l in head.splitlines() if l.startswith("description:"))[len("description:"):].strip()
    meant = json.loads(line) if line.startswith('"') else line
    if yaml is not None:
        return yaml.safe_load(head)["description"], meant
    return (meant if line.startswith('"') or not re.search(r" #|: ", line) else None), meant


class Triggers(unittest.TestCase):
    def test_every_description_reads_whole(self):
        found = skills()
        self.assertIn(os.path.join(ENGINE, "skills", "update", "SKILL.md"), found)
        for path in found:
            got, meant = description(path)
            self.assertEqual(got, meant, os.path.relpath(path, ENGINE))

    def test_every_sentence_sent_to_setup_is_in_the_description(self):
        said = set()
        for name in ("update.py", "update_go.py"):
            said |= set(SAY.findall(read("altention", name)))
        self.assertIn("引擎更新卡住了，幫我處理", said, "the pattern still finds the notices' sentences")
        got, _ = description(os.path.join(ENGINE, "skills", "update", "SKILL.md"))
        for sentence in sorted(said):
            self.assertIn("「%s」" % sentence, got or "", sentence)


if __name__ == "__main__":
    unittest.main()
