"""No row of a transcript makes a reader fail (stage 3b step 2.2d): rows as Claude Code writes them, with one node
at a time made something else (nothing, a number, a string, a list, an object), are put before every function of
the connector that reads rows. A row whose message is no object, a block whose text is no string, an origin or an
attachment that is no object: passed over. (A list of conversations, a history, a turn shown for the owner, what
Claude said since: one such row made the whole of it fail.)"""
import copy
import json
import os
import tempfile
import unittest
from unittest import mock

from altention.connectors.claude_code import hooks, sessions, transcript

CWD = "/w/proj"
ODD = [None, 5, "s", ["a"], {"k": 1}, True, 1.5]


def user(**kw):
    return dict({"type": "user", "uuid": "u1", "promptId": "p1", "timestamp": "2026-10-13T01:00:00Z", "sessionId": "s1", "cwd": CWD,
                 "origin": {"kind": "human"}, "message": {"role": "user", "content": "typed it"}}, **kw)


NOTICE = "<task-notification><task-id>b1</task-id><summary>done</summary><status>completed</status></task-notification>"
ROWS = [
    user(),
    user(message={"role": "user", "content": [{"type": "text", "text": "typed in blocks"}, {"type": "image", "source": {}}]}),
    user(message={"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": [{"type": "text", "text": "out"}]}]}, origin=None),
    user(origin={"kind": "task-notification"}, message={"role": "user", "content": NOTICE}),
    user(isMeta=True), user(isSidechain=True, agentId="a1"),
    {"type": "assistant", "uuid": "a1", "timestamp": "2026-10-13T01:00:01Z",
     "message": {"role": "assistant", "model": "claude-x", "usage": {"input_tokens": 5, "cache_read_input_tokens": 7, "cache_creation_input_tokens": 1},
                 "content": [{"type": "text", "text": "said it"}, {"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "ls"}}]}},
    {"type": "attachment", "timestamp": "2026-10-13T01:00:02Z", "attachment": {"type": "queued_command", "prompt": "sent midway", "commandMode": "prompt", "timestamp": "x"}},
    {"type": "attachment", "attachment": {"type": "queued_command", "prompt": NOTICE.replace("b1", "b2"), "commandMode": "task-notification"}},
    {"type": "ai-title", "aiTitle": "A title", "sessionId": "s1"},
    {"type": "system", "subtype": "compact_boundary", "content": "Conversation compacted"},
    {"type": "user", "promptId": "p2", "message": {"role": "user", "content": "[Workflow harness — user request] do"}},
]
FIRST = user(uuid="u0", promptId="p0", message={"role": "user", "content": "opening"})
LAST = user(uuid="u9", promptId="p9", message={"role": "user", "content": "closing"})


def nodes(node, at=()):
    yield at
    for key, value in node.items() if isinstance(node, dict) else enumerate(node) if isinstance(node, list) else ():
        for found in nodes(value, at + (key,)):
            yield found


def put(row, at, value):
    if not at:
        return value
    row = copy.deepcopy(row)
    node = row
    for key in at[:-1]:
        node = node[key]
    node[at[-1]] = value
    return row


def written(row):
    return json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n"  # as Claude Code writes a row


class OddRows(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        env = mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": os.path.join(tmp.name, "conf"), "HOME": os.path.join(tmp.name, "home")})
        env.start()
        self.addCleanup(env.stop)
        os.makedirs(sessions.project_dir(CWD))
        self.path = os.path.join(sessions.project_dir(CWD), "s1.jsonl")

    def write(self, *rows):
        with open(self.path, "w", encoding="utf-8") as f:
            f.write("".join(written(r) for r in rows))

    def readers(self, row):
        t, path = transcript, self.path

        def events():
            pos = {}
            for ev in ({"hook_event_name": "UserPromptSubmit", "prompt": "typed it"}, {"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_use_id": "t1"},
                       {"hook_event_name": "PostToolUse", "tool_name": "Bash", "tool_use_id": "t1"}, {"hook_event_name": "Stop", "last_assistant_message": "x"},
                       {"hook_event_name": "SubagentStop", "agent_id": "a1", "agent_transcript_path": path}):
                hooks.enrich(dict(ev, transcript_path=path, session_id="s1", prompt_id="p1"), pos)

        def turn():
            for item in t.slim(t.turn(path, 0, None)):
                t.tool_call(item[1], item[2]) if item[0] == "tool" else t.result_text(item[1]) if item[0] == "result" else None
        of_files = [lambda: t.new_texts(path, 0), lambda: t.new_texts(path, 0, sidechain=True), lambda: list(t.rows(path)),
                    lambda: [t.find_begin(path, p) for p in ("p1", "p9", "none")], lambda: t.calls_before(path, os.path.getsize(path)),
                    lambda: [t.turn_end(path, 0, p) for p in ("p0", "p1", None)], lambda: t.context(path), lambda: t.first_prompt(path), lambda: t.asked_yet(path), turn,
                    lambda: sessions.summarize(path), lambda: sessions.turns(path, last=None), lambda: sessions.recent(CWD), lambda: sessions.find(CWD, "s1"),
                    lambda: sessions.live_session(CWD, "closing\n> "), events]
        of_rows = [lambda: t.said_in(row), lambda: t.said_in(row, sidechain=True), lambda: t.asked_as(row), lambda: t.begins(row), lambda: t.calls(row, "t1"),
                   lambda: t.calls_in(row), lambda: sessions.turn_start(row, set())]
        return of_files + (of_rows if isinstance(row, dict) else [])

    def test_no_reader_fails_on_a_row_with_one_node_of_another_type(self):
        tried = 0
        for n, row in enumerate(ROWS):
            for at in nodes(row):
                for value in ODD:
                    odd = put(row, at, value)
                    self.write(FIRST, odd, LAST)
                    for i, read in enumerate(self.readers(odd)):
                        try:
                            read()
                        except Exception as e:
                            self.fail("reader %d failed on row %d with %s = %r: %r" % (i, n, "/".join(map(str, at)) or "the row", value, e))
                        tried += 1
        self.assertGreater(tried, 15000)

    def test_the_rows_around_an_odd_one_are_read_as_ever(self):
        """Passed over: the odd row, not the file. A list of conversations still counts what was typed, a history
        still has its turns, the context still comes from the last row that has one."""
        whole = [FIRST, ROWS[6], ROWS[0], put(ROWS[6], ("message", "content", 0, "text"), "said again"), LAST]
        self.write(*whole)
        base = (sessions.summarize(self.path)["prompts"], [x[:3] for x in sessions.turns(self.path, last=None)], transcript.context(self.path),
                transcript.new_texts(self.path, 0)[0])
        self.assertEqual(base, (3, [["asked", "opening", "said it"], ["asked", "typed it", "said again"], ["asked", "closing", ""]], [13, "claude-x"], ["said it", "said again"]))
        for odd in ({"type": "user", "message": "not an object"}, {"type": "assistant", "message": 5}, {"type": "user", "origin": 5, "message": {"content": 7}},
                    {"type": "assistant", "message": {"usage": "none", "content": [{"type": "text", "text": 5}]}}, {"type": "attachment", "attachment": "x"},
                    {"type": "assistant", "message": {"model": "claude-x", "usage": {"input_tokens": "many"}, "content": 3}}, {"type": "ai-title", "aiTitle": 5}, "a row", 5, None, ["x"]):
            for where in (1, 3, 4):
                self.write(*(whole[:where] + [odd] + whole[where:]))
                got = (sessions.summarize(self.path)["prompts"], [x[:3] for x in sessions.turns(self.path, last=None)], transcript.context(self.path),
                       transcript.new_texts(self.path, 0)[0])
                self.assertEqual(got, base, (odd, where))
                self.assertEqual(sessions.summarize(self.path)["title"], "")

    def test_what_counts_as_context(self):
        row = ROWS[6]
        self.write(row)
        self.assertEqual(transcript.context(self.path), [13, "claude-x"])
        for usage, tokens in (({"input_tokens": 5, "cache_read_input_tokens": "7"}, 5), ({"input_tokens": 5, "cache_read_input_tokens": None}, 5), ({"input_tokens": True}, None),
                              ({"input_tokens": 1234.0, "cache_read_input_tokens": 7}, 1241), ({"input_tokens": 5.9}, 5), ({"input_tokens": 0.0}, None),
                              ({"input_tokens": float("nan"), "cache_read_input_tokens": 7}, 7), ({"input_tokens": float("inf"), "cache_read_input_tokens": 7}, 7),
                              ("none", None), (None, None), ({"input_tokens": [1]}, None)):
            self.write(put(row, ("message", "usage"), usage))
            self.assertEqual(transcript.context(self.path), tokens and [tokens, "claude-x"], usage)


if __name__ == "__main__":
    unittest.main()
