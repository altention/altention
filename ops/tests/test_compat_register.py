"""The public version carries no compatibility code that is not registered (docs/compat.json, after-release), by
the same rule ops/release.py check applies: every COMPAT comment (# or //, then COMPAT and a colon) outside .md
files, matched by file and the text after the colon."""
import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))))
from altention import testing as _testing  # noqa: E402
_testing.guard_home()  # tests never touch the real home (altention/testing.py)
import json
import os
import re
import subprocess
import unittest

ENGINE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
LINE = re.compile(r"(?:#|//)\s*COMPAT:(.*)$")


class Register(unittest.TestCase):
    def comments(self):
        r = subprocess.run(["git", "-C", ENGINE, "grep", "-I", "-n", "-E", "(#|//)[[:space:]]*COMPAT:", "--", ".",
                            ":(exclude)*.md"], stdout=subprocess.PIPE, universal_newlines=True)
        if r.returncode not in (0, 1):
            self.skipTest("not a git checkout")
        return {(l.split(":", 2)[0], LINE.search(l.split(":", 2)[2]).group(1).strip()) for l in r.stdout.splitlines()}

    def test_every_comment_is_registered_after_release(self):
        with open(os.path.join(ENGINE, "docs", "compat.json"), encoding="utf-8") as f:
            entries = json.load(f)
        self.assertIsInstance(entries, list)
        for e in entries:
            self.assertTrue(all(isinstance(e.get(k), str) and e[k] for k in ("file", "text", "status", "why")), e)
            self.assertEqual(e["status"], "after-release", e)
        registered = {(e["file"], e["text"]) for e in entries}
        found = self.comments()
        self.assertEqual(sorted(found - registered), [], "COMPAT comments not in docs/compat.json")
        self.assertEqual(sorted(registered - found), [], "entries whose comment is gone: take them out")

    def test_no_old_engine_place_or_command_names_in_the_code(self):
        r = subprocess.run(["git", "-C", ENGINE, "grep", "-I", "-n", "-E", r"~/agent-home|bin/dc(post|read|notify)|"
                            r"~/agent-logs|~/pm-work|chatlog/host", "--", ".", ":(exclude)*/tests/*",
                            ":(exclude)tests/*", ":(exclude).gitignore", ":(exclude)docs/compat.json", ":(exclude)docs/refactor",
                            ":(exclude)CHANGELOG.md"], stdout=subprocess.PIPE,
                           universal_newlines=True)
        if r.returncode not in (0, 1):
            self.skipTest("not a git checkout")
        self.assertEqual([l for l in r.stdout.splitlines() if not LINE.search(l)], [], "registered ones aside")


if __name__ == "__main__":
    unittest.main()
