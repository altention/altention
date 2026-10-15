"""A broken settings file pushed into the instance never stops the instance's own sync: that sync is the way its fix
arrives (#code-review-2, manager 2026-10-06). machines.json, plugins.json, the instance's CLAUDE.global.md and
privacy.json, each cut short and pushed, then fixed: the next round has the fix, and this machine's own memory is
still committed and pushed. The engine's sync goes on all the while. (machines.json and plugins.json cut short never
come in at all: the settings check holds them back, test_instance_check.py.)"""
import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))))
from altention import testing as _testing  # noqa: E402
_testing.guard_home()  # tests never touch the real home (altention/testing.py)
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

from altention import config, sync

GOOD = {
    "machines.json": json.dumps({"format": 1, "machines": {"box": {"ssh": ["local"], "roles": ["chatlog"]}}}) + "\n",
    "plugins.json": json.dumps({"format": 1, "core": {"timezone": "Asia/Taipei"}, "sync": {"dirs": {}}}) + "\n",
    "CLAUDE.global.md": "# 我的全域說明\n",
    "privacy.json": json.dumps({"names": ["box"]}) + "\n",
}


def git(path, *args, check=True):
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com", "-C", path] + list(args),
                          check=check, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)


class Rig(unittest.TestCase):
    """An engine and an instance, each a bare repo, a clone pushing as another machine (seed) and this machine's."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="壞設定 ")
        self.repos = {}
        for name in ("engine", "instance"):
            bare = os.path.join(self.tmp, name + ".git")
            git(self.tmp, "init", "-q", "--bare", bare)
            git(bare, "symbolic-ref", "HEAD", "refs/heads/main")
            seed = os.path.join(self.tmp, name + "-seed")
            os.makedirs(seed)
            if name == "instance":
                os.makedirs(os.path.join(seed, "memory"))
                for f, text in GOOD.items():
                    with open(os.path.join(seed, f), "w") as fh:
                        fh.write(text)
                with open(os.path.join(seed, "memory", "MEMORY.md"), "w") as fh:
                    fh.write("")
            else:
                with open(os.path.join(seed, "README"), "w") as fh:
                    fh.write("engine\n")
            for args in (["init", "-q"], ["checkout", "-q", "-b", "main"], ["add", "-A"], ["commit", "-qm", "one"],
                         ["remote", "add", "origin", bare], ["push", "-q", "-u", "origin", "main"]):
                git(seed, *args)
            here = os.path.join(self.tmp, name)
            git(self.tmp, "clone", "-q", bare, here)
            git(here, "config", "user.name", "box")
            git(here, "config", "user.email", "box@example.com")
            self.repos[name] = (bare, seed, here)
        self.env = mock.patch.dict(os.environ, {"ALT_INSTANCE": self.repos["instance"][2]})
        self.env.start()
        self.engine = mock.patch.object(config, "ENGINE", self.repos["engine"][2])
        self.engine.start()
        self.verdict = mock.patch.object(sync, "not_this_engine", return_value="")
        self.verdict.start()

    def tearDown(self):
        self.verdict.stop()
        self.engine.stop()
        self.env.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def push_from_elsewhere(self, f, text, msg):
        seed = self.repos["instance"][1]
        git(seed, "pull", "-q", "--rebase")
        with open(os.path.join(seed, f), "w") as fh:
            fh.write(text)
        git(seed, "add", "-A")
        git(seed, "commit", "-qm", msg)
        git(seed, "push", "-q")
        return git(seed, "rev-parse", "HEAD").stdout.strip()

    def instance_file(self, f):
        with open(os.path.join(self.repos["instance"][2], f)) as fh:
            return fh.read()



class BrokenPushed(Rig):
    def test_each_broken_file_then_its_fix(self):
        for f in GOOD:
            with self.subTest(file=f):
                self.push_from_elsewhere(f, GOOD[f][: len(GOOD[f]) // 2], "breaks %s" % f)
                sync.run()  # every machine gets the broken one, but the two the settings check holds back
                self.assertEqual(self.instance_file(f), GOOD[f] if f in sync.SETTINGS else GOOD[f][: len(GOOD[f]) // 2])
                fixed = GOOD[f].replace("box", "box") + ("" if f.endswith(".md") else "")
                fix = self.push_from_elsewhere(f, fixed, "fixes %s" % f)
                with open(os.path.join(self.repos["instance"][2], "memory", "from-box-%s.md" % f), "w") as fh:
                    fh.write("this machine remembers\n")
                st = sync.run()  # the next round
                self.assertEqual(self.instance_file(f), fixed, "%s: the fix did not arrive (%s)" % (f, st.get("instance")))
                here = self.repos["instance"][2]
                self.assertEqual(git(here, "merge-base", "--is-ancestor", fix, "HEAD", check=False).returncode, 0)
                bare_log = git(self.repos["instance"][0], "log", "--format=%s", "-5", "main").stdout
                self.assertIn("sync: memory/knowledge", bare_log, "%s: this machine's memory was not pushed" % f)
                self.assertTrue((st.get("engine") or {}).get("ok"), "%s: the engine's sync stopped: %s" % (f, st.get("engine")))

    def test_a_broken_machine_list_is_written_down(self):
        self.push_from_elsewhere("machines.json", "{ broken", "breaks the list")
        sync.run()  # held back by the settings check; a person takes it (apply.py's way out)
        repo = sync.Repo(self.repos["instance"][2])
        with sync.locked(repo):
            code, note, held = sync.update_instance(repo, take=True)
        self.assertEqual((code, held), (0, ""))
        self.assertIn("越過了被擋的", note)
        st = sync.run()
        self.assertIn("config_error", st)
        with open(os.path.expanduser("~/.local/state/altention/alerts")) as fh:
            self.assertIn("實例照樣同步", fh.read())


if __name__ == "__main__":
    unittest.main()
