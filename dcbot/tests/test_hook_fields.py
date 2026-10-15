"""What Claude Code hands a hook reaches the bot as it was handed: the fields tools/dchook and tools/dcremote add
never bear a name of Claude Code's own (manager 2026-10-11, after the pane written into "source" hid SessionStart's
own "source": a /resume inside a window no longer had its history posted). Held against real payloads of
Claude Code 2.1.296 (the connector's samples: altention/tests/connectors/claude_code/hook_payloads)."""
import importlib.machinery
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from tests import tmuxguard  # noqa: F401,E402  (no test reaches a real tmux server)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PAYLOADS = os.path.join(os.path.dirname(ROOT), "altention", "tests", "connectors", "claude_code", "hook_payloads", "2.1.296")


def load(name, path):
    loader = importlib.machinery.SourceFileLoader(name, path)
    mod = importlib.util.module_from_spec(importlib.util.spec_from_loader(name, loader))
    loader.exec_module(mod)
    return mod


dchook = load("dchook_fields", os.path.join(ROOT, "tools", "dchook"))
dcremote = load("dcremote_fields", os.path.join(ROOT, "tools", "dcremote"))


def payloads():
    for name in sorted(n for n in os.listdir(PAYLOADS) if n.endswith(".json")):
        with open(os.path.join(PAYLOADS, name), encoding="utf-8") as f:
            yield name, json.load(f)


class Fields(unittest.TestCase):
    def through_the_hook(self, payload, pane=True):
        root = tempfile.mkdtemp(prefix="hook ")
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        env = {k: v for k, v in os.environ.items() if not k.startswith(("DCBOT_", "TMUX"))}
        env.update(DCBOT_ROOT=root, DCBOT_CHANNEL="5")
        if pane:
            env.update(TMUX="/tmp/tmux-1/default,123,0", TMUX_PANE="%5")
        r = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "dchook")], input=json.dumps(payload).encode(), env=env,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.assertEqual((r.returncode, r.stdout, r.stderr), (0, b"", b""))
        spool = os.path.join(root, "state", "events")
        (name,) = os.listdir(spool)
        with open(os.path.join(spool, name), encoding="utf-8") as f:
            return json.load(f)

    def ours(self, key):
        return key.startswith(dchook.OURS_PREFIX) or key in dchook.OURS_BEFORE

    def test_the_samples_cover_the_events_a_turn_is_made_of(self):
        kinds = set(p["hook_event_name"] for _, p in payloads())
        self.assertLessEqual({"SessionStart", "UserPromptSubmit", "PreToolUse", "PostToolUse", "Stop", "SessionEnd"}, kinds)
        self.assertEqual(sorted(p.get("source") for _, p in payloads() if p["hook_event_name"] == "SessionStart"), ["clear", "startup"])

    def test_every_field_claude_code_sent_arrives_unchanged(self):
        for name, payload in payloads():
            got = self.through_the_hook(payload)
            for key, value in payload.items():
                if key in dchook.TAKEN_OUT:
                    self.assertNotIn(key, got, (name, key))
                else:
                    self.assertEqual(got.get(key, "MISSING"), value, (name, key))
            added = sorted(k for k in got if k not in payload)
            self.assertTrue(all(self.ours(k) for k in added), (name, added))
            self.assertEqual(got["dc_pane"], {"socket": "/tmp/tmux-1/default", "pane": "%5"}, name)
            self.assertEqual(self.through_the_hook(payload, pane=False).get("dc_pane"), None, name)

    def test_no_name_of_ours_is_one_of_claude_codes(self):
        theirs = set(k for _, p in payloads() for k in p)
        self.assertEqual([k for k in theirs if self.ours(k)], [], "what the hook adds")
        self.assertEqual(sorted(theirs & set(dcremote.ADDED + ("host",))), [], "what the relay on another machine and the stream add")
        self.assertEqual(sorted(set(dcremote.ADDED) & set(dchook.OURS_BEFORE)), [])

    def test_a_long_reply_is_cut_and_says_so_everything_else_stays(self):
        payload = dict(next(p for _, p in payloads() if p["hook_event_name"] == "Stop"), last_assistant_message="x" * (dchook.MAX_TEXT + 5))
        got = self.through_the_hook(payload)
        self.assertTrue(got["last_assistant_message"].startswith("x" * dchook.MAX_TEXT + "\n…（已截斷"))
        self.assertEqual({k: v for k, v in got.items() if k in payload and k != "last_assistant_message"},
                         {k: v for k, v in payload.items() if k != "last_assistant_message"})

    def test_the_relay_on_another_machine_adds_only_its_own(self):
        tmp = tempfile.mkdtemp(prefix="relay ")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        transcript = os.path.join(tmp, "s.jsonl")
        with open(transcript, "w") as f:
            f.write(json.dumps({"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "said"}],
                                                                 "usage": {"input_tokens": 5}, "model": "m"}}) + "\n")
        pos = {}
        for name, payload in payloads():
            ev = dict(payload, transcript_path=transcript, channel="5")
            before = dict(ev)
            if ev["hook_event_name"] == "Stop":
                with open(transcript, "a") as f:  # something was said since the last event
                    f.write(json.dumps({"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "more"}]}}) + "\n")
            dcremote.enrich(ev, pos)
            self.assertEqual({k: v for k, v in ev.items() if k in before}, before, name)
            self.assertLessEqual(set(ev) - set(before), set(dcremote.ADDED), name)
        self.assertEqual((ev.get("texts"), ev.get("context")), (None, None), "the last sample is a SessionEnd: nothing read for it")


if __name__ == "__main__":
    unittest.main()
