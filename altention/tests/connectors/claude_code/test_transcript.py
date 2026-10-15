"""Claude Code's transcript and its subagents' files read into plain data (transcript.py). The tests of new_texts,
context and first_prompt came from the bot's test_sessions, test_tmux and test_agents as they were (stage 3b
step 1.4); those of locate and turn are new; rows, asked_as, said_in, calls and turn_end
came with step 1.6a."""
import json
import os
import tempfile
import unittest

from altention.connectors.claude_code import transcript


def line(kind, content, **extra):
    return json.dumps(dict({"type": kind, "message": {"role": kind, "content": content}}, **extra),
                      ensure_ascii=False, separators=(",", ":")) + "\n"


def human(text):
    return line("user", text, origin={"kind": "human"})


# A turn with every kind of row turn() has a branch for, and rows it must pass over. The bot's test_process holds
# what its read_turn made of this before the reading half moved here: the same pieces after.
EVERY_KIND = (
    line("user", "  asked as a plain string  ")
    + line("user", "   ")
    + line("user", [{"type": "text", "text": "<system-reminder>not the owner's words</system-reminder>"},
                    {"type": "text", "text": " asked as a block "}, {"type": "text", "text": "  "}, "not a block",
                    {"type": "image", "source": {}}])
    + line("assistant", [{"type": "thinking", "thinking": "hm"}, {"type": "text", "text": " said first "},
                         {"type": "text", "text": " \n"}, "not a block",
                         {"type": "tool_use", "name": "Bash", "input": {"command": "ls ```x```", "description": "List"}},
                         {"type": "tool_use", "input": "not a dict"}])
    + line("user", [{"type": "tool_result", "content": "plain ```result```"},
                    {"type": "tool_result", "content": [{"type": "text", "text": "in blocks"}, {"type": "image"}]},
                    {"type": "tool_result", "content": "", "is_error": True},
                    {"type": "tool_result", "content": ""},
                    {"type": "tool_result"}])
    + line("assistant", [{"type": "text", "text": "a subagent's own line"}], isSidechain=True)
    + line("user", "said inside a subagent", isSidechain=True)
    + "this row is not JSON\n"
    + json.dumps(["a", "list", "not", "an", "object"]) + "\n"
    + json.dumps({"type": "assistant"}) + "\n"
    + json.dumps({"type": "system", "message": {"content": "a system row"}}) + "\n"
    + line("assistant", [{"type": "tool_use", "name": "Write", "input": {"file_path": "/a", "content": "abc"}},
                         {"type": "text", "text": "x" * 3100}])
    + line("assistant", "a string where blocks are expected")
)


class NewTexts(unittest.TestCase):
    def test_reads_only_new_complete_lines(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "t.jsonl")
            first = human("hi") + line("assistant", [{"type": "thinking", "thinking": "hmm"}]) \
                + line("assistant", [{"type": "text", "text": " 我先看一下 "}]) \
                + line("assistant", [{"type": "tool_use", "name": "Bash", "input": {}}]) \
                + line("assistant", [{"type": "text", "text": "subagent"}], isSidechain=True)
            half = line("assistant", [{"type": "text", "text": "第二句"}])
            with open(path, "w") as f:
                f.write(first + half[:20])
            texts, pos = transcript.new_texts(path, 0)
            self.assertEqual(texts, ["我先看一下"])
            self.assertEqual(pos, len(first.encode()))
            self.assertEqual(transcript.new_texts(path, pos), ([], pos))  # the half-written line waits
            with open(path, "a") as f:
                f.write(half[20:])
            self.assertEqual(transcript.new_texts(path, pos)[0], ["第二句"])
            self.assertEqual(transcript.size(path), len((first + half).encode()))
            self.assertEqual(transcript.new_texts(os.path.join(tmp, "missing"), 7), ([], 7))
            self.assertEqual(transcript.size(os.path.join(tmp, "missing")), 0)

    def test_a_subagents_own_file_is_read_when_asked(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "agent-a1.jsonl")
            with open(path, "w") as f:
                f.write(line("assistant", [{"type": "text", "text": "from the side"}], isSidechain=True))
            self.assertEqual(transcript.new_texts(path, 0)[0], [])
            self.assertEqual(transcript.new_texts(path, 0, sidechain=True)[0], ["from the side"])


class Context(unittest.TestCase):
    def test_context_is_read_from_the_last_main_reply(self):
        rows = [{"type": "assistant", "message": {"model": "claude-haiku-4-5", "usage": {
                    "input_tokens": 4, "cache_read_input_tokens": 99000, "cache_creation_input_tokens": 996, "output_tokens": 50}}},
                {"type": "assistant", "isSidechain": True, "message": {"model": "x", "usage": {"input_tokens": 7}}},
                {"type": "assistant", "message": {"model": "<synthetic>", "usage": {"input_tokens": 0}}},
                {"type": "user", "message": {"content": "hi"}}]
        with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as f:
            f.write("\n".join(json.dumps(r) for r in rows) + "\n")
        ctx = transcript.context(f.name)
        os.remove(f.name)
        self.assertEqual(ctx, [100000, "claude-haiku-4-5"])
        self.assertIsNone(transcript.context("/nonexistent"))
        self.assertIsNone(transcript.context(None))

    def test_window(self):
        self.assertEqual(transcript.window("claude-haiku-4-5"), 200000)
        self.assertEqual(transcript.window("claude-opus-5-5"), 1000000)


class Subagents(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.transcript = os.path.join(tmp.name, "s1.jsonl")
        self.subdir = os.path.join(tmp.name, "s1", "subagents")

    def agent_files(self, aid, meta, lines, run=None):
        folder = os.path.join(self.subdir, "workflows", run) if run else self.subdir
        os.makedirs(folder, exist_ok=True)
        with open(os.path.join(folder, "agent-%s.meta.json" % aid), "w") as f:
            f.write(meta if isinstance(meta, str) else json.dumps(meta))
        with open(os.path.join(folder, "agent-%s.jsonl" % aid), "a") as f:
            f.write(lines)
        return os.path.join(folder, "agent-%s.jsonl" % aid)

    def test_workflow_prompt_loses_the_harness_notice(self):
        raw = ("[Workflow harness — computed task] The task text below was computed… The computed task text follows:\n"
               "  Run the thing.\n  Then reply RED")
        self.assertEqual(transcript.task_text(raw), "Run the thing.\nThen reply RED")
        self.assertEqual(transcript.task_text("plain prompt"), "plain prompt")
        path = self.agent_files("w9", {}, line("user", raw), run="wf_9")
        self.assertEqual(transcript.first_prompt(path), "Run the thing.\nThen reply RED")

    RELAY = ("[Workflow harness — user request] The harness relays, verbatim and indented below, the user request that "
             "triggered this workflow run. …\n  please look into the two folders")
    TASK = ("[Workflow harness — computed task] The task text below was computed at runtime by a workflow script. … The "
            "computed task text follows:\n  Count the files.\n  Then reply with the number.")

    def test_a_workflow_agents_task_is_its_own_not_the_relayed_request(self):
        """#issues 28 (Claude Code 2.1.296, a recorded run): the harness first relays the owner's request, the same
        words for every agent of the run, and only then gives the agent its task. The thread's "task" was the relay:
        the harness's own notice and the owner's message, alike for all agents."""
        path = self.agent_files("w1", {}, line("user", self.RELAY) + line("user", self.TASK) + line("user", "a later message"), run="wf_1")
        self.assertEqual(transcript.first_prompt(path), "Count the files.\nThen reply with the number.")
        blocks = self.agent_files("w2", {}, line("user", [{"type": "text", "text": self.RELAY}]) + line("user", [{"type": "text", "text": self.TASK}]),
                                  run="wf_1")
        self.assertEqual(transcript.first_prompt(blocks), "Count the files.\nThen reply with the number.")

    def test_where_the_harness_brings_no_task_the_first_row_stands_as_before(self):
        """A version that words its rows otherwise, a file whose second row is not written: what it was."""
        only = self.agent_files("w3", {}, line("user", self.RELAY), run="wf_1")
        self.assertEqual(transcript.first_prompt(only), self.RELAY)
        then_plain = self.agent_files("w4", {}, line("user", self.RELAY) + line("user", "something else said to it"), run="wf_1")
        self.assertEqual(transcript.first_prompt(then_plain), self.RELAY, "the next row is no task of the harness's: the first one stands")
        old = self.agent_files("w5", {}, line("user", self.TASK) + line("user", "later"), run="wf_1")
        self.assertEqual(transcript.first_prompt(old), "Count the files.\nThen reply with the number.", "a task in the first row, as it always was")
        plain = self.agent_files("a9", {}, line("user", "an ordinary subagent's task") + line("user", self.TASK))
        self.assertEqual(transcript.first_prompt(plain), "an ordinary subagent's task", "not a workflow's agent: its first row, nothing looked for further")
        empty = self.agent_files("a8", {}, line("user", "") + line("user", "said after an empty one"))
        self.assertEqual(transcript.first_prompt(empty), "", "an empty first message: empty, as before")

    def test_first_prompt_in_blocks_and_none(self):
        path = self.agent_files("a1", {}, line("assistant", [{"type": "text", "text": "not this"}])
                                + line("user", [{"type": "text", "text": "<system-reminder>x</system-reminder>"}])
                                + line("user", [{"type": "text", "text": " the task "}]) + line("user", "a later one"))
        self.assertEqual(transcript.first_prompt(path), "the task")
        self.assertEqual(transcript.first_prompt(os.path.join(self.subdir, "missing.jsonl")), "")

    def test_where_a_subagents_files_are(self):
        self.assertIsNone(transcript.locate(self.transcript, "a1"), "not written yet")
        path = self.agent_files("a1", {"description": "Look around"}, "")
        self.assertEqual(transcript.locate(self.transcript, "a1"), ({"description": "Look around"}, path, None))
        inner = self.agent_files("w1", {"description": "One of a run"}, "", run="wf_7")
        self.assertEqual(transcript.locate(self.transcript, "w1"), ({"description": "One of a run"}, inner, "wf_7"))
        broken = self.agent_files("a2", "{not json", "")
        self.assertEqual(transcript.locate(self.transcript, "a2"), ({}, broken, None))
        self.assertEqual(transcript.session_dir("/p/s1.jsonl"), "/p/s1")
        self.assertEqual(transcript.session_dir("/p/s1"), "/p/s1")


class Turn(unittest.TestCase):
    def test_every_kind_of_row(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "t.jsonl")
            with open(path, "w", encoding="utf-8") as f:
                f.write(EVERY_KIND)
            items = transcript.turn(path, 0, None)
            self.assertEqual([i[0] for i in items], ["asked", "asked", "said", "tool", "tool", "result", "result", "result",
                                                     "result", "result", "tool", "said"])
            self.assertEqual(items[0], ("asked", "  asked as a plain string  "), "as written: trimming is the caller's")
            self.assertEqual(items[1], ("asked", " asked as a block "))
            self.assertEqual(items[3], ("tool", "Bash", {"command": "ls ```x```", "description": "List"}))
            self.assertEqual(items[4], ("tool", "?", "not a dict"))
            self.assertEqual(items[7], ("result", {"type": "tool_result", "content": "", "is_error": True}))
            self.assertEqual(items[9], ("result", {"type": "tool_result"}))
            self.assertIsNone(transcript.turn(os.path.join(tmp, "gone.jsonl"), 0, 10))

    def test_only_between_the_two_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "t.jsonl")
            before, mid, after = human("an earlier turn"), human("this one"), human("a later turn")
            with open(path, "w") as f:
                f.write(before + mid + after)
            start = len(before.encode())
            self.assertEqual(transcript.turn(path, start, start + len(mid.encode())), [("asked", "this one")])
            self.assertEqual(transcript.turn(path, start, None), [("asked", "this one"), ("asked", "a later turn")])
            self.assertEqual(transcript.turn(path, start, start), [], "an end at the start: nothing")
            self.assertEqual(len(transcript.turn(path, 0, 0)), 3, "no end recorded (0): to the end of the file, as before")



class Rows(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = os.path.join(tmp.name, "t.jsonl")

    def write(self, text):
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(text)

    def test_whole_rows_with_their_bytes(self):
        first, second = line("user", "一", promptId="p1"), "not json\n"
        third = json.dumps(["a", "list"]) + "\n"
        half = line("assistant", [{"type": "text", "text": "half"}])
        self.write(first + second + third + half[:10])
        rows = transcript.rows(self.path)
        self.assertEqual([(a, b) for a, b, _ in rows], [(0, len(first.encode())), (len(first.encode()), len((first + second).encode())),
                                                       (len((first + second).encode()), len((first + second + third).encode()))])
        self.assertEqual([row and row.get("type") for _, _, row in rows], ["user", None, None], "what is no JSON object: None")
        self.assertEqual(transcript.rows(self.path, rows[1][0])[0][0], rows[1][0])
        self.write(half[10:])
        self.assertEqual(len(transcript.rows(self.path)), 4, "the row being written waits for its newline")
        self.assertEqual(transcript.rows(os.path.join(self.path, "missing")), [])
        self.assertEqual(transcript.rows(None), [])

    def test_what_a_row_is(self):
        asked = json.loads(line("user", "x", promptId="p1"))
        self.assertEqual(transcript.asked_as(asked), "p1")
        self.assertIsNone(transcript.asked_as(dict(asked, isSidechain=True)), "a subagent's own: not the main conversation's")
        self.assertIsNone(transcript.asked_as(json.loads(line("user", "x"))))
        self.assertIsNone(transcript.asked_as(json.loads(line("assistant", [], promptId="p1"))))
        self.assertIsNone(transcript.asked_as(None))
        call = json.loads(line("assistant", [{"type": "text", "text": " said "}, {"type": "tool_use", "id": "t1", "name": "Bash", "input": {}}]))
        self.assertEqual((transcript.calls(call, "t1"), transcript.calls(call, "t2"), transcript.calls(None, "t1")), (True, False, False))
        self.assertFalse(transcript.calls(dict(call, isSidechain=True), "t1"))
        self.assertEqual(transcript.said_in(call), ["said"])
        self.assertEqual((transcript.said_in(dict(call, isSidechain=True)), transcript.said_in(dict(call, isSidechain=True), sidechain=True)), ([], ["said"]))
        for odd in ({"type": "assistant"}, {"type": "assistant", "message": "a string"}, {"type": "assistant", "message": {"content": "a string"}},
                    {"type": "assistant", "message": {"content": [{"type": "text", "text": 5}, "not a block", {"type": "text", "text": "  "}]}}):
            self.assertEqual((transcript.said_in(odd), transcript.calls(odd, "t1")), ([], False), odd)

    def test_where_a_turn_ends(self):
        one = line("user", "one", promptId="p1") + line("assistant", [{"type": "text", "text": "a"}]) \
            + line("user", [{"type": "tool_result", "tool_use_id": "t", "content": "ok"}], promptId="p1") \
            + line("user", "a subagent's", promptId="p9", isSidechain=True) + line("assistant", [{"type": "text", "text": "reply"}])
        two = line("user", "two", promptId="p2") + line("assistant", [{"type": "text", "text": "b"}])
        self.write(one + two)
        self.assertEqual(transcript.turn_end(self.path, 0), len(one.encode()), "not at its own tool result, not at a side chain's row")
        self.assertIsNone(transcript.turn_end(self.path, len(one.encode())), "the last turn: to the end of the file, as it is then")
        mid = len(line("user", "one", promptId="p1").encode())
        self.assertEqual(transcript.turn_end(self.path, mid), len(one.encode()), "a start past the turn's first row (no record of it): its id is the first one met")
        self.write(line("user", "three", promptId="p3")[:20])
        self.assertIsNone(transcript.turn_end(self.path, len(one.encode())), "a row still being written is not there yet")
        self.assertIsNone(transcript.turn_end(os.path.join(self.path, "missing"), 0))

    def test_where_a_turn_ends_by_its_own_id(self):
        """With the turn's id the end does not depend on where the start lies: also past the turn's last row under
        its own id (where, without the id, the next turn's id is the first one met and the NEXT turn is shown)."""
        one = line("user", "one", promptId="p1") + line("user", [{"type": "tool_result", "tool_use_id": "t", "content": "ok"}], promptId="p1")
        tail = line("assistant", [{"type": "text", "text": "reply one"}])
        two = line("user", "two", promptId="p2") + line("user", [{"type": "tool_result", "tool_use_id": "u", "content": "ok"}], promptId="p2") \
            + line("assistant", [{"type": "text", "text": "reply two"}])
        three = line("user", "three", promptId="p3")
        self.write(one + tail + two + three)
        past = len(one.encode())  # after the last row under p1
        end_of_one, end_of_two = len((one + tail).encode()), len((one + tail + two).encode())
        self.assertEqual(transcript.turn_end(self.path, past), end_of_two, "without the id: the next turn is taken for this one")
        self.assertEqual(transcript.turn_end(self.path, past, "p1"), end_of_one)
        self.assertEqual(transcript.turn_end(self.path, 0, "p1"), end_of_one)
        self.assertEqual(transcript.turn_end(self.path, end_of_one, "p2"), end_of_two, "its own tool result is not another turn")
        self.assertIsNone(transcript.turn_end(self.path, end_of_two, "p3"))
        self.assertEqual(transcript.turn_end(self.path, 0, "an id no row has"), 0, "every message row is another turn's then: nothing")


class Slim(unittest.TestCase):
    """A turn made small enough to send to another machine (stage 3b step 1.10)."""

    def test_a_tools_output_is_its_text_cut_with_how_long_it_was(self):
        blocks = {"type": "tool_result", "tool_use_id": "t1", "content": [{"type": "text", "text": " ab"}, {"type": "image", "source": {"data": "x" * 9000}},
                                                                             {"type": "text", "text": "cd \n"}], "is_error": True}
        items = [("asked", "q"), ("tool", "Bash", {"command": "c" * 9000}), ("result", blocks), ("result", {"content": "y" * 10}),
                 ("result", {"content": None}), ("result", "not a block"), ("said", "s" * 9000)]
        out = transcript.slim(items, result=4)
        self.assertEqual(out[0], ("asked", "q"))
        self.assertEqual(out[1], items[1], "a tool call as it is: what it shows of itself counts its own letters")
        self.assertEqual(out[2], ("result", {"content": "ab\nc", "is_error": True, "chars": 5}))
        self.assertEqual(out[3], ("result", {"content": "yyyy", "is_error": False, "chars": 10}))
        self.assertEqual(out[4], ("result", {"content": "", "is_error": False, "chars": 0}))
        self.assertEqual(out[5], ("result", {"content": "", "is_error": False, "chars": 0}))
        self.assertEqual(out[6], items[6])
        self.assertEqual(items[2][1]["tool_use_id"], "t1", "the turn given is not changed")
        json.dumps(out)
        self.assertEqual(transcript.result_text(blocks), "ab\ncd")

    def test_beyond_the_whole_it_stops_and_counts(self):
        items = [("said", "x" * 100)] * 10
        self.assertEqual(transcript.slim(items), items)
        out = transcript.slim(items, total=350)
        self.assertEqual((len(out), out[-1]), (4, ("more", 7)))
        self.assertEqual(transcript.slim(items, total=1), [("more", 10)])
        self.assertEqual(transcript.slim([]), [])
        self.assertGreaterEqual(transcript.SLIM_RESULT, 3000, "more than the reader shows of one output (the bot: 1200)")


class WholeRowsOnly(unittest.TestCase):
    """A subagent's file read while its row is being written (#code-review, 4dba78c; stage 3b step 1.10): a row
    without its newline is not there yet, for what the agent was asked as for whether it has been told."""

    def rows(self, *rows):
        return "".join(json.dumps(r, ensure_ascii=False, separators=(",", ":")) + "\n" for r in rows)

    def test_a_task_row_half_written_is_not_counted(self):
        relay = {"type": "user", "message": {"role": "user", "content": transcript.HARNESS + "user request] do the thing"}}
        task = {"type": "user", "message": {"role": "user", "content": transcript.HARNESS + "computed task] x " + transcript.FOLLOWS + "\nsay A"}}
        whole = self.rows(relay, task)
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "agent.jsonl")
            for cut in (len(self.rows(relay)) + 30, len(whole) - 1):  # within the second row, and all of it but its newline
                with open(path, "w") as f:
                    f.write(whole[:cut])
                self.assertFalse(transcript.asked_yet(path), cut)
                self.assertNotIn("say A", transcript.first_prompt(path))
            with open(path, "w") as f:
                f.write(whole)
            self.assertTrue(transcript.asked_yet(path))
            self.assertEqual(transcript.first_prompt(path), "say A")
            with open(path, "w") as f:
                f.write(self.rows({"type": "user", "message": {"role": "user", "content": "a plain task"}})[:-1])
            self.assertEqual((transcript.asked_yet(path), transcript.first_prompt(path)), (False, ""), "its only row, not ended")
            with open(path, "w") as f:
                f.write(self.rows(relay) + self.rows({"type": "assistant", "message": {"role": "assistant", "content": []}})[:-1])
            self.assertFalse(transcript.asked_yet(path), "nor an answer of its that is still being written")


class ToolCalls(unittest.TestCase):
    """What a tool call is, as data (stage 3b step 2.2d): the tools' names and where in their input the thing is
    are known here; the words are the reader's."""

    def test_each_sort(self):
        t = transcript
        self.assertEqual(t.tool_call("Bash", {"command": "ls", "description": "List"}), (t.COMMAND, {"command": "ls", "note": "List"}))
        self.assertEqual(t.tool_call("Bash", {}), (t.COMMAND, {"command": "", "note": None}))
        self.assertEqual(t.tool_call("Read", {"file_path": "/a"}), (t.FILE, {"path": "/a", "old": None, "new": None, "chars": None}))
        self.assertEqual(t.tool_call("Edit", {"file_path": "/a", "old_string": "abc", "new_string": "de"})[1], {"path": "/a", "old": 3, "new": 2, "chars": None})
        self.assertEqual(t.tool_call("Edit", {"file_path": "/a"})[1], {"path": "/a", "old": 0, "new": 0, "chars": None}, "an Edit with nothing given is still an Edit")
        self.assertEqual(t.tool_call("Write", {"file_path": "/a", "content": "four"})[1], {"path": "/a", "old": None, "new": None, "chars": 4})
        self.assertEqual(t.tool_call("NotebookEdit", {"notebook_path": "/n.ipynb"})[1]["path"], "/n.ipynb")
        self.assertEqual(t.tool_call("Read", {})[1]["path"], None)
        self.assertEqual(t.tool_call("Grep", {"pattern": "x", "path": "/src"}), (t.SEARCH, {"pattern": "x", "path": "/src"}))
        self.assertEqual(t.tool_call("Glob", {}), (t.SEARCH, {"pattern": "", "path": None}))
        self.assertEqual(t.tool_call("Agent", {"description": "Look", "prompt": "find"}), (t.AGENT, {"description": "Look", "prompt": "find"}))
        self.assertEqual(t.tool_call("Task", {"prompt": "find"}), (t.AGENT, {"description": None, "prompt": "find"}))
        self.assertEqual(t.tool_call("WebFetch", {"url": "https://x"}), (t.WEB, {"what": "https://x"}))
        self.assertEqual(t.tool_call("WebSearch", {"query": "q"}), (t.WEB, {"what": "q"}))
        self.assertEqual(t.tool_call("WebSearch", {}), (t.WEB, {"what": ""}))

    def test_a_tool_not_known_here_keeps_its_input_and_a_broken_input_is_an_empty_one(self):
        t = transcript
        self.assertEqual(t.tool_call("mcp__x__y", {"a": 1}), (t.OTHER, {"input": {"a": 1}}))
        self.assertEqual(t.tool_call("Workflow", {"script": "s"}), (t.OTHER, {"input": {"script": "s"}}))
        self.assertEqual(t.tool_call(None, {"command": "ls"}), (t.OTHER, {"input": {"command": "ls"}}), "by the name, never by what the input looks like")
        for broken in (None, "text", ["a"], 3):
            self.assertEqual(t.tool_call("Something", broken), (t.OTHER, {"input": {}}))
            self.assertEqual(t.tool_call("Bash", broken), (t.COMMAND, {"command": "", "note": None}))


class OwnTranscripts(unittest.TestCase):
    """Is a path a transcript of Claude Code's own on this machine: for a command that reads a file for whoever
    can run it (tools/dcremote turn)."""

    def test_only_a_jsonl_under_the_projects_folder_by_its_real_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = os.path.realpath(tmp)
            conf = os.path.join(tmp, "conf")
            proj = os.path.join(conf, "projects", "-w-proj")
            os.makedirs(os.path.join(proj, "s1", "subagents"))
            os.makedirs(os.path.join(tmp, "elsewhere"))
            inside, sub = os.path.join(proj, "s1.jsonl"), os.path.join(proj, "s1", "subagents", "agent-a1.jsonl")
            outside, other = os.path.join(tmp, "elsewhere", "x.jsonl"), os.path.join(proj, "notes.txt")
            for path in (inside, sub, outside, other, os.path.join(conf, "settings.jsonl")):
                open(path, "w").close()
            os.symlink(outside, os.path.join(proj, "link.jsonl"))
            os.symlink(proj, os.path.join(tmp, "way-in"))

            def own(path):
                return transcript.own(path, conf)
            self.assertEqual((own(inside), own(sub)), (inside, sub))
            self.assertEqual(own(os.path.join(tmp, "way-in", "s1.jsonl")), inside, "a link into the folder: the real file")
            self.assertEqual(own(os.path.join(proj, "not-written.jsonl")), os.path.join(proj, "not-written.jsonl"), "a file not there is the reader's to find gone")
            for no in (outside, other, os.path.join(proj, "link.jsonl"), os.path.join(proj, "..", "..", "settings.jsonl"),
                       os.path.join(proj, "..", "..", "..", "elsewhere", "x.jsonl"), os.path.join(conf, "projects"), "", "/etc/passwd"):
                self.assertIsNone(own(no), no)
            os.symlink(conf, os.path.join(tmp, "conf-link"))
            self.assertEqual(transcript.own(inside, os.path.join(tmp, "conf-link")), inside, "the settings folder itself reached through a link")


class BrokenRows(unittest.TestCase):
    """Rows and inputs not shaped as Claude Code shapes them (a broken file, another program's rows): passed over or
    written as they are, by every reader here; none of them fails (a 📋 that ends in silence)."""

    ROWS = [{"type": "user", "promptId": "p1", "message": {"role": "user", "content": "first"}},
            {"type": "user", "message": "not an object"}, {"type": "assistant", "message": ["a", "list"]}, {"type": "assistant", "message": 5},
            {"type": "user", "message": {"content": [{"type": "text", "text": 7}, {"type": "text", "text": None}, {"type": "text"}, {"type": "text", "text": ["x"]},
                                                     {"type": "text", "text": "typed after"}, "a string", 3, None]}},
            {"type": "assistant", "message": {"content": [{"type": "text", "text": {"k": 1}}, {"type": "text", "text": 0}, {"type": "text", "text": "said it"},
                                                          {"type": "tool_use", "name": "Bash", "input": {"command": 5}}, {"type": "tool_use"}, {"type": "tool_use", "name": 9, "input": "x"}]}},
            {"type": "user", "message": {"content": [{"type": "tool_result", "content": [{"type": "text", "text": 4}, {"type": "text", "text": "out"}, {"type": "text"}]},
                                                     {"type": "tool_result", "content": 12}, {"type": "tool_result"}]}},
            {"type": "assistant", "message": {"content": "a string where blocks go"}}, {"type": "assistant", "message": {"content": 8}}, {"type": "assistant"},
            "a row that is a string", 4, None, ["a", "list"]]

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, "s.jsonl")
        with open(self.path, "w") as f:
            f.write("".join(json.dumps(r, separators=(",", ":")) + "\n" for r in self.ROWS) + "not json\n")  # (as Claude Code writes rows)

    def test_a_turn_is_read_past_them(self):
        items = transcript.turn(self.path, 0, None)
        self.assertEqual([i[:2] for i in items if i[0] in ("asked", "said")], [("asked", "first"), ("asked", "typed after"), ("said", "said it")])
        self.assertEqual([i for i in items if i[0] == "tool"], [("tool", "Bash", {"command": 5}), ("tool", "?", None), ("tool", 9, "x")])
        self.assertEqual([transcript.result_text(i[1]) for i in items if i[0] == "result"], ["out", "12", ""])
        small = json.loads(json.dumps(transcript.slim(items)))
        self.assertEqual(len(small), len(items))

    def test_every_other_reader_of_rows_gets_past_them(self):
        t = transcript
        size = os.path.getsize(self.path)
        self.assertIsNone(t.turn_end(self.path, 0, "p1"), "no row of another turn: to the end of the file")
        self.assertEqual(t.new_texts(self.path, 0), (["said it"], size))
        self.assertEqual(t.new_texts(self.path, 0, sidechain=True)[1], size)
        self.assertEqual(t.first_prompt(self.path), "first")
        self.assertTrue(t.asked_yet(self.path))
        t.context(self.path), t.find_begin(self.path, "p1"), t.calls_before(self.path, size)
        for row in self.ROWS:
            if isinstance(row, dict):
                t.asked_as(row), t.begins(row), t.said_in(row), t.said_in(row, sidechain=True), t.calls(row, "id"), t.calls_in(row)

    def test_the_helpers(self):
        t = transcript
        self.assertEqual([t.content_of(r) for r in ({"message": {"content": "c"}}, {"message": "m"}, {"message": None}, {}, "row", None, 3)], ["c", None, None, None, None, None, None])
        self.assertEqual([t.text_of(b) for b in ({"type": "text", "text": "x"}, {"type": "text", "text": 5}, {"type": "text"}, {"type": "image", "text": "x"}, "b", None)], ["x", "", "", "", "", ""])
        self.assertEqual([t.written(v) for v in ("s", None, 5, 0, ["a"], {"k": 1}, True)], ["s", "", "5", "0", "['a']", "{'k': 1}", "True"])

    def test_a_tools_input_of_any_shape_is_data(self):
        t = transcript
        ODD = [None, 5, 0, 1.5, True, ["a", "b"], {"k": 1}, "text", ""]
        for name in ("Bash", "Read", "Write", "Edit", "NotebookEdit", "Grep", "Glob", "Agent", "Task", "WebFetch", "WebSearch", "Other", None, 5):
            for v in ODD:
                for inp in (v, {k: v for k in ("command", "description", "file_path", "notebook_path", "old_string", "new_string", "content", "pattern", "path", "prompt", "url", "query")}):
                    sort, what = t.tool_call(name, inp)
                    json.dumps(what)
        self.assertEqual(t.tool_call("Bash", {"command": 5})[1]["command"], "5")
        self.assertEqual(t.tool_call("Edit", {"old_string": 12345, "new_string": ["x"]})[1], {"path": None, "old": 5, "new": 5, "chars": None})
        self.assertEqual(t.tool_call("Write", {"content": {"k": 1}})[1]["chars"], 8)
        self.assertEqual(t.tool_call("Agent", {"prompt": 7})[1]["prompt"], "7")


class Stretches(unittest.TestCase):
    """The whole text of one field of one item of a turn, and a stretch of it (stage 3b step 2.3b: a reader that
    showed only the beginning of a long field asks for the rest)."""

    ITEMS = [("asked", "  hello\nworld  "), ("said", "a\n```py\ncode 1\ncode 2\n```\nafter"), ("tool", "Bash", {"command": " ls -l ", "description": "List"}),
             ("result", {"content": [{"type": "text", "text": "out 1"}, {"type": "text", "text": "out 2"}]}), ("tool", "Agent", {"prompt": "find it"}),
             ("tool", "Workflow", {"script": "s", "n": 1}), ("tool", "Read", {"file_path": "/a"}), ("more", 3)]

    def test_the_whole_of_each_field(self):
        t, items = transcript, self.ITEMS
        self.assertEqual([t.whole(items, n, f) for n, f in ((0, t.TEXT), (1, t.TEXT), (2, t.COMMAND_TEXT), (3, t.TEXT), (4, t.PROMPT_TEXT), (5, t.INPUT_TEXT))],
                         ["hello\nworld", "a\n```py\ncode 1\ncode 2\n```\nafter", "ls -l", "out 1\nout 2", "find it", '{"script": "s", "n": 1}'])
        for n, f in ((0, t.COMMAND_TEXT), (2, t.TEXT), (2, t.PROMPT_TEXT), (4, t.COMMAND_TEXT), (6, t.INPUT_TEXT), (6, t.TEXT), (7, t.TEXT), (8, t.TEXT), (-1, t.TEXT),
                     (0, "other"), ("0", t.TEXT), (None, t.TEXT), (True, t.TEXT)):
            self.assertIsNone(t.whole(items, n, f), (n, f))
        self.assertEqual(t.whole(json.loads(json.dumps(items)), 3, t.TEXT), "out 1\nout 2", "also of a turn that went through JSON")
        self.assertIsNone(t.whole([None, "x", ["asked"]], 2, t.TEXT))

    def test_stretches_put_together_are_the_whole(self):
        t = transcript
        prose = "\n".join("line %d 這一行有中文" % i for i in range(400)) + "\n```\ncode\n```\nend"
        code = "".join(chr(0x4e00 + i % 500) for i in range(5000))
        for items, n, field, limit in (([("said", prose)], 0, t.TEXT, 700), ([("asked", prose)], 0, t.TEXT, 33), ([("result", {"content": code})], 0, t.TEXT, 999),
                                       ([("tool", "Bash", {"command": code})], 0, t.COMMAND_TEXT, 1), ([("said", "no newline at all " * 50)], 0, t.TEXT, 100)):
            whole, got, at, n_stretches = t.whole(items, n, field), "", 0, 0
            while True:
                p = t.piece(items, n, field, at, limit)
                self.assertEqual((p["total"], p["prose"]), (len(whole), items[0][0] in ("asked", "said")))
                self.assertLessEqual(len(p["text"]), limit)
                self.assertGreater(p["next"], at, "it always goes on")
                got, at, n_stretches = got + p["text"], p["next"], n_stretches + 1
                if at >= p["total"]:
                    break
            self.assertEqual(got, whole, (field, limit))
            self.assertGreater(n_stretches, 1)

    def test_prose_is_cut_at_line_ends_and_says_which_fence_is_open(self):
        t, items = transcript, self.ITEMS
        p = t.piece(items, 1, t.TEXT, 0, 12)
        self.assertEqual((p["text"], p["next"], p["fence"], p["open"]), ("a\n```py", 7, None, "py"), "back to the end of the last whole line")
        p = t.piece(items, 1, t.TEXT, 7, 100)
        self.assertEqual((p["text"], p["fence"], p["open"], p["next"] == p["total"]), ("\ncode 1\ncode 2\n```\nafter", "py", None, True))
        p = t.piece(items, 2, t.COMMAND_TEXT, 1, 2)
        self.assertEqual(p, {"text": "s ", "total": 5, "next": 3, "prose": False, "fence": None, "open": None, "of": {"kind": "tool", "tool": "Bash", "last": False}},
                         "what is no prose: no fence told")
        self.assertEqual([t.source(items, n) for n in (0, 1, 3, 5, 7, 99)],
                         [{"kind": "asked", "tool": None, "last": False}, {"kind": "said", "tool": None, "last": True}, {"kind": "result", "tool": "Bash", "last": False},
                          {"kind": "tool", "tool": "Workflow", "last": False}, {"kind": "more", "tool": None, "last": False}, {"kind": None, "tool": None, "last": False}],
                         "a result is its tool's: the call it follows")
        self.assertEqual(t.source([("result", {"content": "x"})], 0), {"kind": "result", "tool": None, "last": False})
        self.assertEqual([t.source([("said", "on the way"), ("tool", "Bash", {}), ("said", "the reply")], n)["last"] for n in (0, 2)], [False, True])
        self.assertEqual(t.fence_at("x\n```\ny", 99), "", "a fence with no word after it")
        self.assertEqual([t.fence_at("b\n```\nc", at, "js") for at in (0, 1, 5, 7)], ["js", "js", None, None], "a stretch that begins inside a fence")
        self.assertEqual(t.fence_at("b\nc", 3, ""), "")
        self.assertEqual([t.fence_at("a\n ```js \nb\n```\nc", at) for at in (0, 2, 9, 12, 17)], [None, None, "js", "js", None])
        self.assertEqual([t.line_end("ab\ncd\nef", at) for at in (0, 1, 2, 3, 4, 5, 7, 8, 99)], [0, 1, 2, 2, 2, 5, 5, 8, 8])
        self.assertEqual(t.line_end("ab\ncd\nef", 7, floor=5), 7, "no line end after where the stretch begins: cut where it is")
        numbers = "\n".join(str(i) for i in range(1000))
        for start, limit in ((0, 1200), (1197, 1850), (1500, 1000), (10, 7)):
            end = t.cut_at(numbers, start, limit)
            self.assertTrue(numbers[end] == "\n" and start + limit // 2 < end <= start + limit, (start, limit, end), )
        self.assertEqual(t.cut_at("x" * 50 + "\n" + "y" * 5000, 0, 1000), 1000, "a line end too far back: cut where it is, not to a tenth")
        self.assertEqual(t.cut_at("no newline at all", 3, 5), 8)
        self.assertEqual(t.cut_at("short", 0, 100), 5)
        self.assertEqual(t.piece([("result", {"content": numbers})], 0, t.TEXT, 0, 1200)["text"][-4:], "\n326", "an output is cut at a line's end too")

    def test_what_is_not_there(self):
        t, items = transcript, self.ITEMS
        for args in ((6, t.TEXT, 0, 10), (99, t.TEXT, 0, 10), (0, t.TEXT, "0", 10), (0, t.TEXT, 0, None), (0, t.TEXT, True, 10)):
            self.assertIsNone(t.piece(items, *args), args)
        self.assertEqual(t.piece(items, 0, t.TEXT, 999, 10)["text"], "", "past its end: nothing, and it says how long the whole is")
        self.assertEqual(t.piece(items, 0, t.TEXT, -5, 3)["text"], "hel")


if __name__ == "__main__":
    unittest.main()
