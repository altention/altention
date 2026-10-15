"""A conversation file as Claude Code writes it, for tests that read one.

The lines have the shape seen in real transcripts (Claude Code 2.1.29x, read on 2026-10-11; the words in them are
made up): one JSON object per line; a reply is one line per content block (thinking, text, tool_use), all of one
request sharing its message id and its usage; a prompt is a "user" line whose content is a string; a tool result
is a "user" line whose content is a list; and between them lines that are no message at all (mode,
file-history-snapshot, attachment, system, queue-operation, ai-title, last-prompt), which every reader must skip.
"""
import datetime
import json
import os
import uuid

MODEL = "claude-opus-5-5"
USAGE = {"input_tokens": 2, "cache_creation_input_tokens": 2420, "cache_read_input_tokens": 695172, "output_tokens": 295,
         "service_tier": "standard"}
TOKENS = 2 + 2420 + 695172  # what the connector's transcript.context() counts for USAGE: 70% of this model's window


def now():
    """The time a row is written, as Claude Code stamps it. (A fixed moment stood here until stage 3b step 2.4d: a row
    written after an event and stamped days before it is what a row copied from another conversation looks like, and
    is read as one.)"""
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class Transcript(object):
    def __init__(self, path, session="0f6e1c1e-1111-4222-8333-444455556666", cwd="/w"):
        self.path, self.session, self.cwd, self.last, self.request = path, session, cwd, None, 0
        os.makedirs(os.path.dirname(path), exist_ok=True)
        open(path, "a").close()

    def size(self):
        return os.path.getsize(self.path)

    def raw(self, d):
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(d, ensure_ascii=False) + "\n")

    def line(self, kind, **more):
        d = {"parentUuid": self.last, "isSidechain": False}
        d.update(more)
        d.update({"type": kind, "uuid": str(uuid.uuid4()), "timestamp": now(), "userType": "external",
                  "entrypoint": "cli", "cwd": self.cwd, "sessionId": self.session, "version": "2.1.295", "gitBranch": "main"})
        self.last = d["uuid"]
        self.raw(d)
        return d

    def opened(self):
        """What a session writes before anything is said."""
        self.raw({"type": "mode", "mode": "normal", "sessionId": self.session})
        self.raw({"type": "permission-mode", "permissionMode": "bypassPermissions", "sessionId": self.session})

    def prompt(self, text, prompt_id=None):
        self.raw({"type": "file-history-snapshot", "messageId": str(uuid.uuid4()),
                  "snapshot": {"messageId": "m", "trackedFileBackups": {}}})
        return self.line("user", promptId=prompt_id or str(uuid.uuid4()), message={"role": "user", "content": text},
                         permissionMode="bypassPermissions", origin={"kind": "human"}, promptSource="typed")

    def queued(self, text):
        """A message sent while a turn runs waits in the queue."""
        self.raw({"type": "queue-operation", "operation": "enqueue", "timestamp": now(),
                  "sessionId": self.session, "content": text})

    def block(self, block, stop="tool_use", usage=None, model=MODEL, **more):
        return self.line("assistant", message={"model": model, "id": "msg_%02d" % self.request, "type": "message",
                                               "role": "assistant", "content": [block], "stop_reason": stop,
                                               "stop_sequence": None, "usage": dict(usage or USAGE)}, **more)

    def think(self):
        self.request += 1
        return self.block({"type": "thinking", "thinking": "", "signature": "sig"})

    def say(self, text, stop="tool_use"):
        return self.block({"type": "text", "text": text}, stop)

    def tool(self, name, tool_input, tool_id=None):
        tid = tool_id or "toolu_%s" % uuid.uuid4().hex[:20]
        self.block({"type": "tool_use", "id": tid, "name": name, "input": tool_input, "caller": {"type": "direct"}})
        return tid

    def result(self, tool_id, text, error=False, prompt_id=None):
        """A tool's result: a row said to Claude under the promptId of the turn it is in (measured on 2.1.288 and
        2.1.296; until stage 3b step 1.6a this stand-in gave each a random one, which nothing read then)."""
        return self.line("user", promptId=prompt_id or str(uuid.uuid4()),
                         message={"role": "user", "content": [{"tool_use_id": tool_id, "type": "tool_result", "content": text,
                                                               "is_error": error}]},
                         toolUseResult={"stdout": text, "stderr": "", "interrupted": False}, sourceToolAssistantUUID=self.last)

    def noise(self):
        """Lines between the messages that are none: a reminder attached for the model, the hooks' summary, the
        title, and a subagent's line (a side chain: never the conversation's own words)."""
        self.line("attachment", attachment={"type": "silent_turn_reminder", "text": "a reminder only the model reads"},
                  rendered=[{"content": "a reminder only the model reads"}], renderedRole="system")
        self.line("system", subtype="stop_hook_summary", hookCount=3, hookInfos=[{"command": "dchook", "durationMs": 63}],
                  hookErrors=[], preventedContinuation=False, level="suggestion")
        self.raw({"type": "ai-title", "aiTitle": "a-title", "sessionId": self.session})
        self.block({"type": "text", "text": "a subagent's words, never the conversation's"}, isSidechain=True)
        self.raw({"type": "last-prompt", "lastPrompt": "x", "leafUuid": self.last, "sessionId": self.session})
