"""Every git job of the engine takes the same lock file: a holder spelling another name would lock nothing."""
import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))))
from altention import testing as _testing  # noqa: E402
_testing.guard_home()  # tests never touch the real home (altention/testing.py)
import re
import subprocess
import unittest

from altention import sync, update

ENGINE = _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
HOLDERS = ["altention/sync.py", "altention/update.py", "altention/cron_sync.py", "altention/cron_update.py",
           "dcbot/deploy.sh", "dcbot/deploy_candidate.py", "dcbot/supervisor.py"]


class LockName(unittest.TestCase):
    def test_one_name(self):
        self.assertEqual(sync.LOCK_NAME, "altention-git.lock")
        self.assertEqual(update.LOCK_NAME, sync.LOCK_NAME)

    def test_every_holder_spells_it(self):
        for rel in HOLDERS:
            with open(_os.path.join(ENGINE, rel), encoding="utf-8") as f:
                text = f.read()
            self.assertIn(sync.LOCK_NAME, text, rel)

    def test_every_place_that_takes_the_lock_names_it(self):
        """Each place a file in the git dir is opened to be flocked: `open(os.path.join(<the git dir>, "<name>"), "a")`
        in Python, `$(git rev-parse --git-common-dir)/<name>` in shell. Every such name is LOCK_NAME, whatever it
        looks like (a holder renamed to lock-for-git must fail here); deploy.sh's own deploy lock is the one other."""
        py = re.compile(r'open\(os\.path\.join\(.*(?:\bgd\b|git_dir\(\)).*,\s*"([^"]+)"\),\s*"a"\)')
        shell = re.compile(r'--git-common-dir\)/([^"\s]+)"')
        seen = {}
        for rel in HOLDERS:
            with open(_os.path.join(ENGINE, rel), encoding="utf-8") as f:
                for n, line in enumerate(f, 1):
                    for name in py.findall(line) + shell.findall(line):
                        if name == "altention-deploy.lock" and rel == "dcbot/deploy.sh" and line.lstrip().startswith("DLOCK="):
                            continue  # one --commit deploy at a time: a lock of its own, not the git lock
                        seen.setdefault(rel, []).append((n, name))
        self.assertEqual({rel: [x for x in found if x[1] != sync.LOCK_NAME] for rel, found in seen.items()},
                         {rel: [] for rel in seen}, "a lock taken under another name")
        literal = [rel for rel in HOLDERS if rel not in ("altention/sync.py", "altention/update.py")]  # these two: LOCK_NAME
        self.assertEqual(sorted(seen), sorted(literal), "a holder whose lock is no longer found: read it and update the patterns")
        self.assertEqual(len(seen["dcbot/deploy.sh"]), 2, "deploy.sh takes it in two places")

    def test_no_other_git_lock_name_in_the_tree(self):  # tests aside: they name an older engine's lock as data
        r = subprocess.run(["git", "-C", ENGINE, "grep", "-I", "-h", "-o", "-E", r"[A-Za-z0-9_.]+-git\.lock", "--", ".",
                            ":(exclude)*.md", ":(exclude)*/tests/*"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, universal_newlines=True)
        if r.returncode not in (0, 1):
            self.skipTest("not a git checkout")
        names = set(r.stdout.split()) - {sync.LOCK_NAME}
        self.assertEqual(names, set(), "a git lock spelled another way: %s" % sorted(names))


if __name__ == "__main__":
    unittest.main()
