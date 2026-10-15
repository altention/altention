"""A sync where every export tried fails (the engine's redaction does not load beside chatlog) says so: last_error is
written, the log line is not "sync ok", the exit code is 1; doctor shows last_error (#code-review 8b9aef3, low item 1;
manager 2026-10-14). One that goes through is unchanged."""
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

PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "chatlog.py"))
SID = "5e9a0c1d-0000-4000-8000-0000000000a1"
GONE = "5e9a0c1d-0000-4000-8000-0000000000a2"  # only in history.jsonl: rebuilt from it
SECRET = "sk-ant-" + "a" * 30


def rec(kind, i, text):
    msg = {"role": kind, "content": text} if kind == "user" else \
        {"role": kind, "model": "claude-x", "content": [{"type": "text", "text": text}]}
    return {"type": kind, "timestamp": "2026-10-01T00:00:%02dZ" % i, "sessionId": SID, "cwd": "/w/proj",
            "uuid": "%s%d" % (kind[0], i), "message": msg}


class AllFailed(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="chatlog all failed ")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.claude = os.path.join(self.tmp, "claude")
        os.makedirs(os.path.join(self.claude, "projects", "-w-proj"))
        with open(os.path.join(self.claude, "projects", "-w-proj", SID + ".jsonl"), "w") as f:
            for i, (kind, text) in enumerate((("user", "問題 %s" % SECRET), ("assistant", "答"))):
                f.write(json.dumps(rec(kind, i, text)) + "\n")
        with open(os.path.join(self.claude, "history.jsonl"), "w") as f:
            f.write(json.dumps({"display": "舊的問題 %s" % SECRET, "timestamp": 1790000000000, "project": "/w/proj",
                                "sessionId": GONE}) + "\n")
        self.repo = os.path.join(self.tmp, "logs")
        subprocess.run(["git", "init", "-q", self.repo], check=True)
        for k, v in (("user.name", "t"), ("user.email", "t@example.invalid")):
            subprocess.run(["git", "-C", self.repo, "config", k, v], check=True)
        self.state = os.path.join(self.tmp, "state")

    def sync(self, chatlog):
        env = {"PATH": "/usr/bin:/bin", "HOME": self.tmp, "CHATLOG_REPO": self.repo, "CHATLOG_STATE": self.state,
               "CHATLOG_HOST": "host-a", "CLAUDE_CONFIG_DIR": self.claude, "GIT_CONFIG_NOSYSTEM": "1"}
        r = subprocess.run(["/usr/bin/python3", chatlog, "sync"], cwd="/", env=env, stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE, universal_newlines=True, timeout=60)
        with open(os.path.join(self.state, "state.json")) as f:
            st = json.load(f)
        with open(os.path.join(self.state, "chatlog.log")) as f:
            last = f.read().strip().split("\n")[-1]
        return r.returncode, st, last

    def written(self):
        out = []
        for root, dirs, files in os.walk(self.repo):
            dirs[:] = [d for d in dirs if d != ".git"]
            out += [os.path.join(root, f) for f in files]
        return out

    def test_every_export_failing_is_said(self):
        alone = os.path.join(self.tmp, "elsewhere", "chatlog", "chatlog.py")  # no altention beside it
        os.makedirs(os.path.dirname(alone))
        shutil.copy(PATH, alone)
        # the transcript is not marked done: tried again next time, and said again; the rebuild from history.jsonl
        # waits for that file to change (low item 2, left as it was by the manager)
        for count in (2, 1):
            code, st, last = self.sync(alone)
            self.assertEqual(code, 1)
            self.assertTrue(st["last_error"].startswith("export: all %d failed, e.g. " % count), st["last_error"])
            self.assertIn("altention", st["last_error"])
            self.assertNotIn("sync ok", last)
            self.assertIn("export: all %d failed" % count, last)
            self.assertEqual(st["sigs"], {})
        for path in self.written():  # nothing of the conversations, so nothing unredacted
            self.assertNotIn(SID[:8], path)
            with open(path, encoding="utf-8", errors="replace") as f:
                self.assertNotIn(SECRET, f.read())

    def test_going_through_is_unchanged(self):
        code, st, last = self.sync(PATH)
        self.assertEqual((code, st["last_error"]), (0, None))
        self.assertIn("sync ok, 2 session(s) rendered", last)
        names = self.written()
        self.assertTrue(any(SID[:8] in p for p in names) and any(GONE[:8] in p for p in names), names)
        for path in names:
            with open(path, encoding="utf-8", errors="replace") as f:
                self.assertNotIn(SECRET, f.read())


if __name__ == "__main__":
    unittest.main()
