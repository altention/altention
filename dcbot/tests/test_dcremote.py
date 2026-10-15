"""tools/dcremote runs on the other machines: its event relay must remove the spool file, never the transcript."""
import importlib.machinery
import importlib.util
import io
import json
import os
import subprocess
import tempfile
import unittest
from tests import tmuxguard  # noqa: F401,E402  (no test reaches a real tmux server)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_loader = importlib.machinery.SourceFileLoader("dcremote_mod", os.path.join(ROOT, "tools", "dcremote"))
dcremote = importlib.util.module_from_spec(importlib.util.spec_from_loader("dcremote_mod", _loader))
_loader.exec_module(dcremote)


PROBE = """
import importlib.machinery, importlib.util, json, sys
loader = importlib.machinery.SourceFileLoader("dcremote_probe", sys.argv[1])
loader.exec_module(importlib.util.module_from_spec(importlib.util.spec_from_loader("dcremote_probe", loader)))
print(json.dumps(sorted(m for m in sys.modules if m.split(".")[0] in ("bot", "altention", "discord", "aiohttp"))))
"""


class WhatItLoads(unittest.TestCase):
    def test_the_subagents_files_are_read_by_the_connector_not_by_the_bots_threads_module(self):
        """On another machine the tool runs under the system Python, nothing installed: since stage 3b step 1.4 it
        reads a subagent's files with the connector's transcript.py and no longer loads bot/agents.py (the Discord
        threads). A fresh interpreter, so what other tests imported does not count."""
        r = subprocess.run(["/usr/bin/python3", "-c", PROBE, os.path.join(ROOT, "tools", "dcremote")], cwd="/",
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        loaded = json.loads(r.stdout)
        self.assertIn("altention.connectors.claude_code.transcript", loaded)
        self.assertNotIn("bot.agents", loaded)
        self.assertNotIn("bot.process", loaded)
        self.assertEqual([m for m in loaded if m.split(".")[0] in ("discord", "aiohttp")], [])


class Relay(unittest.TestCase):
    def test_spool_file_removed_transcript_kept_and_sent_once(self):
        tmp = tempfile.mkdtemp()
        spool = os.path.join(tmp, "events")
        os.makedirs(spool)
        transcript = os.path.join(tmp, "session.jsonl")
        with open(transcript, "w") as f:
            f.write(json.dumps({"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "hi"}]}}) + "\n")
        for i, ev in enumerate([{"channel": "1", "hook_event_name": "UserPromptSubmit", "transcript_path": transcript},
                                {"channel": "1", "hook_event_name": "Stop", "transcript_path": transcript, "last_assistant_message": "x"},
                                {"channel": "1", "hook_event_name": "Notification"}]):
            with open(os.path.join(spool, "%02d.json" % i), "w") as f:
                json.dump(ev, f)
        out, pos = io.StringIO(), {}
        self.assertEqual(dcremote.relay(spool, pos, out), 3)
        self.assertEqual(os.listdir(spool), [], "every spool file removed")
        self.assertTrue(os.path.exists(transcript), "the conversation itself is never touched")
        self.assertEqual(len(out.getvalue().strip().split("\n")), 3)
        self.assertEqual(dcremote.relay(spool, pos, out), 0, "nothing sent twice")


if __name__ == "__main__":
    unittest.main()


class Broken(unittest.TestCase):
    def test_an_event_that_breaks_reading_still_goes_and_never_blocks_the_queue(self):
        tmp = tempfile.mkdtemp()
        spool = os.path.join(tmp, "events")
        os.makedirs(spool)
        for i in range(2):
            with open(os.path.join(spool, "%02d.json" % i), "w") as f:
                json.dump({"channel": "1", "hook_event_name": "Stop", "transcript_path": "/nonexistent", "n": i}, f)
        out = io.StringIO()
        orig = dcremote.enrich
        dcremote.enrich = lambda ev, pos: (_ for _ in ()).throw(AttributeError("odd line"))
        try:
            self.assertEqual(dcremote.relay(spool, {}, out), 2)
        finally:
            dcremote.enrich = orig
        lines = [json.loads(x) for x in out.getvalue().strip().split("\n")]
        self.assertEqual([x["n"] for x in lines], [0, 1])
        self.assertIn("odd line", lines[0]["relay_error"])
        self.assertEqual(os.listdir(spool), [])

    def test_set_aside_moves_a_bad_event_out_of_the_queue(self):
        from bot import events
        tmp = tempfile.mkdtemp()
        spool = os.path.join(tmp, "events")
        os.makedirs(spool)
        path = os.path.join(spool, "01.json")
        with open(path, "w") as f:
            f.write('{"channel": "1"}')
        events.set_aside(path, spool)
        self.assertEqual(os.listdir(spool), [])
        self.assertEqual(os.listdir(spool + "-bad"), ["01.json"])
