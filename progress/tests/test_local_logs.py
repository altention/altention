"""progress on one machine with no remote (what a new install has): the mirror comes from chatlog's own folder here,
a round runs through (Claude is a stand-in), and a round that stops is noted for doctor (owner's re-install,
2026-10-15: every round stopped on "where are the logs" and doctor said only "not run yet")."""
import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))))
from altention import testing as _testing  # noqa: E402
_testing.guard_home()  # tests never touch the real home (altention/testing.py)
import json
import os
import stat
import subprocess
import sys
import unittest
from datetime import timedelta
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_places import GIT_ID, Base, chatlog_repo, progress, write  # noqa: E402

FAKE_CLAUDE = r'''#!%s
import json, sys
schema = json.loads(sys.argv[sys.argv.index("--json-schema") + 1])
sys.stdin.read()
if "entries" in schema["properties"]:
    out = {"entries": [{"project": "demo", "new_project": {"name": "Demo", "description": "d"}, "summary": "做了示範",
                        "done": ["a"], "in_progress": [], "blockers": [], "decisions": [], "next": [],
                        "why_project": "w", "confidence": "high"}]}
else:
    out = {"state": "active", "headline": "示範進行中", "current": [], "recent_done": ["a"], "blockers": [],
           "decisions": [], "next": []}
print(json.dumps({"structured_output": out, "total_cost_usd": 0}))
''' % sys.executable


class OneMachineNoRemote(Base):
    def setUp(self):
        super().setUp()
        self.instance({})  # an instance with no sync.dirs at all
        self.logs = os.path.join(self.home, "data", "logs")
        claude = os.path.join(self.home, "claude")
        write(claude, FAKE_CLAUDE)
        os.chmod(claude, os.stat(claude).st_mode | stat.S_IXUSR)
        self.state = os.path.join(self.home, "cache", "progress")
        for p in (mock.patch.object(progress.cl, "REPO", self.logs), mock.patch.object(progress.cl, "HOST", "box"),
                  mock.patch.object(progress, "CLAUDE", claude), mock.patch.object(progress, "STATE_DIR", self.state),
                  mock.patch.object(progress, "WORK", os.path.join(self.home, "work")),
                  mock.patch.dict(os.environ, GIT_ID)):
            p.start()
            self.patches.append(p)

    def state_json(self):
        return progress.read_json(os.path.join(self.state, "state.json"), {})

    def add_session(self, sid, text):
        """One conversation as Claude Code writes it, exported and committed by chatlog's own functions."""
        t = progress.now_utc() - timedelta(hours=2)
        recs = [{"type": "user", "sessionId": sid, "cwd": "/tmp/demo", "timestamp": t.isoformat().replace("+00:00", "Z"),
                 "message": {"role": "user", "content": text}},
                {"type": "assistant", "sessionId": sid, "cwd": "/tmp/demo",
                 "timestamp": (t + timedelta(minutes=1)).isoformat().replace("+00:00", "Z"),
                 "message": {"role": "assistant", "content": [{"type": "text", "text": "好，做好了。"}]}}]
        path = os.path.join(self.home, "transcripts", sid + ".jsonl")
        write(path, "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in recs))
        self.assertTrue(progress.cl.export_session(path))
        subprocess.run(["git", "-C", self.logs, "add", "-A"], check=True)
        subprocess.run(["git", "-C", self.logs, "commit", "-q", "-m", sid], check=True)

    def test_a_round_runs_from_chatlogs_folder_here(self):
        chatlog_repo(self.logs)
        self.add_session("11111111-aaaa-bbbb-cccc-000000000001", "幫我做示範")
        old = os.umask(0o022)  # a usual umask: what is made without a mode is 755
        try:
            self.assertEqual(progress.run_noted(), 0)
        finally:
            os.umask(old)
        for private in (progress.LOGS, progress.PROGRESS):  # made by this first round: every conversation, and its summary
            self.assertEqual(stat.S_IMODE(os.stat(private).st_mode), 0o700, private)
        self.assertEqual(progress.origin_of(progress.LOGS), self.logs, "the mirror comes from chatlog's folder here")
        st = self.state_json()
        self.assertTrue(st.get("last_run"))
        self.assertNotIn("last_failed", st)
        self.assertEqual(st.get("last_processed"), 1)
        logs = os.listdir(os.path.join(progress.PROGRESS, "projects", "demo", "log"))
        self.assertIn("做了示範", progress.read(os.path.join(progress.PROGRESS, "projects", "demo", "log", logs[0])))
        # the next conversation reaches the next round through the same folder
        self.add_session("11111111-aaaa-bbbb-cccc-000000000002", "再做一個")
        self.assertEqual(progress.run_noted(), 0)
        self.assertEqual(self.state_json().get("last_processed"), 1)

    def test_a_new_install_with_no_conversation_yet_is_a_quiet_round(self):
        chatlog_repo(self.logs, commit=False)
        self.assertEqual(progress.run_noted(), 0)
        self.assertTrue(self.state_json().get("last_run"))
        self.assertFalse(os.path.exists(progress.LOGS))

    def test_no_chatlog_here_and_no_remote_is_noted_in_plain_words(self):
        with self.assertRaises(SystemExit) as e:
            progress.run_noted()
        said = str(e.exception.code)
        self.assertIn("進度要跟對話紀錄在同一台機器上", said)
        self.assertIn("sync.dirs.chatlog.remote", said)
        failed = self.state_json()["last_failed"]
        self.assertTrue(failed["at"])
        self.assertIn("進度要跟對話紀錄在同一台機器上", failed["why"])
        # once chatlog is here, the next round ends well and clears it
        chatlog_repo(self.logs)
        self.assertEqual(progress.run_noted(), 0)
        self.assertNotIn("last_failed", self.state_json())

    def test_a_progress_repo_cloned_from_its_remote_is_private_too(self):
        chatlog_repo(self.logs)
        bare = os.path.join(self.home, "progress.git")
        subprocess.run(["git", "init", "-q", "--bare", bare], check=True)
        seed = chatlog_repo(os.path.join(self.home, "seed"))
        subprocess.run(["git", "-C", seed, "push", "-q", bare, "main"], check=True)
        old = os.umask(0o022)
        try:
            with mock.patch.dict(os.environ, {"PROGRESS_REMOTE": bare}):
                self.assertEqual(progress.run_noted(), 0)
        finally:
            os.umask(old)
        self.assertTrue(os.path.isfile(os.path.join(progress.PROGRESS, "README.md")) or
                        os.path.isdir(os.path.join(progress.PROGRESS, ".git")))
        self.assertEqual(progress.origin_of(progress.PROGRESS), bare)
        self.assertEqual(stat.S_IMODE(os.stat(progress.PROGRESS).st_mode), 0o700)

    def test_a_remote_set_later_is_where_the_mirror_fetches_from(self):
        chatlog_repo(self.logs)
        self.assertEqual(progress.run_noted(), 0)
        bare = os.path.join(self.home, "remote.git")
        subprocess.run(["git", "clone", "-q", "--bare", self.logs, bare], check=True)
        with mock.patch.dict(os.environ, {"PROGRESS_LOGS_REMOTE": bare}):
            self.assertEqual(progress.run_noted(), 0)
            self.assertEqual(progress.origin_of(progress.LOGS), bare)


if __name__ == "__main__":
    unittest.main()
