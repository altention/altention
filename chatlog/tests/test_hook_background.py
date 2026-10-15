"""The hook leaves a background Claude alone (altention/background.py, stage 3b step 1.9b; until then the hook had
a name of its own for it, CHATLOG_SKIP, which the progress plugin had to know)."""
import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))))
from altention import testing as _testing  # noqa: E402
_testing.guard_home()  # tests never touch the real home (altention/testing.py)
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from altention import background

PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "chatlog.py"))
spec = importlib.util.spec_from_file_location("chatlog_hook_bg", PATH)
chatlog = importlib.util.module_from_spec(spec)
spec.loader.exec_module(chatlog)


class Hook(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.transcript = os.path.join(self.tmp.name, "s.jsonl")
        with open(self.transcript, "w") as f:
            f.write(json.dumps({"type": "user", "message": {"role": "user", "content": "hi"}}) + "\n")
        self.state = os.path.join(self.tmp.name, "state")

    def hook(self, env, event="Stop", path=None):
        """cmd_hook() as the hook calls it, with what it would start written down instead of started."""
        started = []
        data = json.dumps({"hook_event_name": event, "transcript_path": self.transcript if path is None else path})
        clean = dict((k, v) for k, v in os.environ.items() if k not in (background.NAME, "CHATLOG_SKIP"))
        with mock.patch.dict(os.environ, dict(clean, **env), clear=True), mock.patch.object(chatlog, "STATE_DIR", self.state), \
                mock.patch.object(chatlog.subprocess, "Popen", lambda args, **kw: started.append(args)), \
                mock.patch.object(sys, "stdin", io.StringIO(data)):
            code = chatlog.cmd_hook()
        return code, started

    def test_an_ordinary_conversation_is_exported(self):
        code, started = self.hook({})
        self.assertEqual((code, len(started)), (0, 1))
        self.assertEqual(started[0][-2:], ["export", self.transcript])
        code, started = self.hook({}, event="SessionEnd")
        self.assertEqual(started[0][-3:], ["export", self.transcript, "--sync"])

    def test_a_background_claude_is_left_alone(self):
        for event in ("Stop", "SessionEnd"):
            self.assertEqual(self.hook(background.env({}), event=event), (0, []))
        self.assertFalse(os.path.exists(self.state), "nothing written either")
        self.assertEqual(self.hook({background.NAME: ""})[1][0][-2:], ["export", self.transcript], "set to nothing: not one")

    def test_the_name_of_its_own_it_had_is_no_longer_read(self):
        code, started = self.hook({"CHATLOG_SKIP": "1"})
        self.assertEqual((code, len(started)), (0, 1), "nothing in the engine sets it any more")
        with open(PATH, encoding="utf-8") as f:
            self.assertNotIn("CHATLOG_SKIP", f.read())

    def test_as_claude_code_runs_it(self):
        """The system Python, another working directory, nothing of this environment: the engine's shared code is
        found beside the plugin's folder. Marked: it ends before reading anything. Not marked, with a transcript that
        is not there: it ends without starting anything."""
        base = {"PATH": "/usr/bin:/bin", "HOME": self.tmp.name, "CHATLOG_STATE": self.state}
        data = json.dumps({"hook_event_name": "Stop", "transcript_path": self.transcript})
        r = subprocess.run(["/usr/bin/python3", PATH, "hook"], input=data, cwd="/", env=background.env(base),
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, timeout=30)
        self.assertEqual((r.returncode, r.stdout, r.stderr), (0, "", ""))
        self.assertFalse(os.path.exists(self.state), "marked: no export was started")
        gone = json.dumps({"hook_event_name": "Stop", "transcript_path": os.path.join(self.tmp.name, "gone.jsonl")})
        r = subprocess.run(["/usr/bin/python3", PATH, "hook"], input=gone, cwd="/", env=base,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, timeout=30)
        self.assertEqual((r.returncode, r.stdout, r.stderr), (0, "", ""))
        self.assertFalse(os.path.exists(self.state))

    def test_without_the_engines_shared_code_beside_it_nothing_is_taken_for_one(self):
        """Not a way it is installed; pinned so that the hook then goes on as for any conversation and does not fail."""
        alone = os.path.join(self.tmp.name, "elsewhere", "chatlog")
        os.makedirs(alone)
        shutil.copy(PATH, os.path.join(alone, "chatlog.py"))
        code = ("import importlib.util, sys\n"
                "spec = importlib.util.spec_from_file_location('c', sys.argv[1]); c = importlib.util.module_from_spec(spec)\n"
                "spec.loader.exec_module(c); print(c.background_claude())\n")
        env = background.env({"PATH": "/usr/bin:/bin", "HOME": self.tmp.name, "CHATLOG_STATE": self.state})
        r = subprocess.run(["/usr/bin/python3", "-c", code, os.path.join(alone, "chatlog.py")], cwd="/", env=env,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, timeout=30)
        self.assertEqual((r.returncode, r.stdout.strip(), r.stderr), (0, "False", ""))
        r = subprocess.run(["/usr/bin/python3", "-c", code, PATH], cwd="/", env=env,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, timeout=30)
        self.assertEqual((r.returncode, r.stdout.strip()), (0, "True"), "beside the engine: it is one")

    def test_shared_code_that_does_not_load_does_not_fail_the_hook(self):
        """A file of the engine's cut short, or one that raises as it loads: the hook of every Claude on the machine
        would end with a traceback. It goes on as for any conversation (here: a transcript that is not there)."""
        gone = json.dumps({"hook_event_name": "Stop", "transcript_path": os.path.join(self.tmp.name, "gone.jsonl")})
        for n, broken in enumerate(("def env(:\n", "raise OSError(13, 'no')\n", "NAME = 'X'\n", "import nosuchmodule_xyz\n")):
            root = os.path.join(self.tmp.name, "engine%d" % n)
            os.makedirs(os.path.join(root, "chatlog"))
            os.makedirs(os.path.join(root, "altention"))
            shutil.copy(PATH, os.path.join(root, "chatlog", "chatlog.py"))
            open(os.path.join(root, "altention", "__init__.py"), "w").close()
            with open(os.path.join(root, "altention", "background.py"), "w") as f:
                f.write(broken)
            env = background.env({"PATH": "/usr/bin:/bin", "HOME": self.tmp.name, "CHATLOG_STATE": self.state})
            r = subprocess.run(["/usr/bin/python3", os.path.join(root, "chatlog", "chatlog.py"), "hook"], input=gone, cwd="/", env=env,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, timeout=30)
            self.assertEqual((r.returncode, r.stdout, r.stderr), (0, "", ""), broken)
        if os.getuid():  # a file that is there and cannot be read (root reads anything)
            os.chmod(os.path.join(root, "altention", "background.py"), 0)
            self.addCleanup(os.chmod, os.path.join(root, "altention", "background.py"), 0o600)
            r = subprocess.run(["/usr/bin/python3", os.path.join(root, "chatlog", "chatlog.py"), "hook"], input=gone, cwd="/", env=env,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, timeout=30)
            self.assertEqual((r.returncode, r.stdout, r.stderr), (0, "", ""), "unreadable")
        self.assertFalse(os.path.exists(self.state))


if __name__ == "__main__":
    unittest.main()
