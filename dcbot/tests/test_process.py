"""📋: a turn read back from the transcript, as Discord pieces."""
import json
import os
import tempfile
import unittest

import bot  # noqa: F401  (first: it puts the engine's folder on the path; the connectors lie beside dcbot/)
from altention.tests.connectors.claude_code.test_transcript import EVERY_KIND
from bot import process
from tests import tmuxguard  # noqa: F401,E402  (no test reaches a real tmux server)


def line(kind, content, **extra):
    return json.dumps(dict({"type": kind, "message": {"role": kind, "content": content}}, **extra), ensure_ascii=False) + "\n"


class Process(unittest.TestCase):
    def test_a_turn_from_the_transcript(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "t.jsonl")
            before = line("user", "an earlier turn") + line("assistant", [{"type": "text", "text": "earlier reply"}])
            turn = (line("user", "check the repo", origin={"kind": "human"})
                    + line("assistant", [{"type": "thinking", "thinking": ""}, {"type": "text", "text": "Looking."}])
                    + line("assistant", [{"type": "tool_use", "name": "Bash", "input": {"command": "git status", "description": "Status"}}])
                    + line("user", [{"type": "tool_result", "content": "On branch main\nclean"}])
                    + line("assistant", [{"type": "tool_use", "name": "Edit", "input": {"file_path": "/a.py", "old_string": "x", "new_string": "yy"}}])
                    + line("user", [{"type": "tool_result", "content": [{"type": "text", "text": "boom"}], "is_error": True}])
                    + line("assistant", [{"type": "text", "text": "Done."}], isSidechain=True)
                    + line("assistant", [{"type": "text", "text": "All good."}]))
            with open(path, "w") as f:
                f.write(before + turn + line("user", "a later turn"))
            start = len(before.encode())
            pieces = process.read_turn(path, start, start + len(turn.encode()))
            self.assertEqual(pieces[0], "🧑 **你**\ncheck the repo")
            self.assertEqual(pieces[1], "💬 Looking.")
            self.assertIn("🔧 **Bash**（Status）\n```\ngit status\n```", pieces[2])
            self.assertIn("On branch main", pieces[3])
            self.assertEqual(pieces[4], "🔧 **Edit** `/a.py`（改 1 字 → 2 字）")
            self.assertTrue(pieces[5].startswith("❌\n```"))
            self.assertEqual(pieces[-1], "💬 All good.")
            self.assertNotIn("later turn", "".join(pieces))
            self.assertNotIn("Done.", "".join(pieces), "a subagent's own lines are not the turn")
            self.assertIsNone(process.read_turn(os.path.join(tmp, "gone.jsonl"), 0, 10))

    def test_the_same_pieces_as_before_the_reading_half_moved(self):
        """read_turn was one function until stage 3b step 1.4; reading the transcript is the connector's now
        (transcript.turn). What the old function made of a turn with every kind of row, taken from it before
        the split, is what the two halves make of it."""
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "t.jsonl")
            with open(path, "w", encoding="utf-8") as f:
                f.write(EVERY_KIND)
            pieces = process.read_turn(path, 0, None)
        self.assertEqual(pieces, [
            '🧑 **你**\nasked as a plain string',
            '🧑 **你**\nasked as a block',
            '💬 said first',
            "🔧 **Bash**（List）\n```\nls '''x'''\n```",
            '🔧 **?** `{}`',
            "```\nplain '''result'''\n```",
            '```\nin blocks\n```',
            '❌\n```\n（沒有輸出）\n```',
            '🔧 **Write** `/a`（3 字）',
            "💬 " + "x" * 3000 + "\n…（還有 100 字，對這則按 📋 接著看）",
        ])

    def test_the_end_is_found_when_asked_and_an_old_record_keeps_its_own(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "t.jsonl")
            first = line("user", "one", promptId="p1") + line("assistant", [{"type": "text", "text": "reply one"}])
            with open(path, "w") as f:
                f.write(first)
            self.assertIsNone(process.turn_end(path, 0, None), "the last turn: to the end of the file")
            self.assertEqual(process.read_turn(path, 0, process.turn_end(path, 0, None)), ["🧑 **你**\none", "💬 reply one"])
            with open(path, "a") as f:
                f.write(line("user", "two", promptId="p2") + line("assistant", [{"type": "text", "text": "reply two"}]))
            self.assertEqual(process.turn_end(path, 0, None), len(first.encode()))
            self.assertEqual(process.read_turn(path, 0, process.turn_end(path, 0, None)), ["🧑 **你**\none", "💬 reply one"],
                             "the same once the next turn is written")
            self.assertEqual(process.turn_end(path, 0, 40), 40, "a record of before step 1.6a: the end it was given")
            st = {}
            process.remember(st, [7], path, 0, None)
            self.assertEqual(st["turns"]["7"], [path, 0, None, None])
            self.assertNotIn("7", st.get("turn_ids") or {}, "no id given: none kept")
            process.remember(st, [8, 9], path, len(first.encode()), None, "p2")
            self.assertEqual((st["turns"]["8"], st["turn_ids"]), ([path, len(first.encode()), None, None], {"8": "p2", "9": "p2"}),
                             "the record keeps its four fields (a version of before reads it); the turn's id lies beside it")
            self.assertEqual(process.pieces_of(st, 8), ["🧑 **你**\ntwo", "💬 reply two"])
            self.assertEqual(process.pieces_of(st, 7), ["🧑 **你**\none", "💬 reply one"])
            self.assertIsNone(process.pieces_of(st, 1234))
            past = len(line("user", "one", promptId="p1").encode())  # a start past the turn's only row under its id
            process.remember(st, [10], path, past, None, "p1")
            self.assertEqual(process.pieces_of(st, 10), ["💬 reply one"], "with the id: its own turn, not the next")
            for n in range(process.KEEP + 5):
                process.remember(st, [1000 + n], path, 0, None, "p1")
            self.assertEqual((len(st["turns"]), len(st["turn_ids"])), (process.KEEP, process.KEEP), "ids go with their records")

    def test_remember_keeps_the_latest(self):
        st = {}
        for i in range(process.KEEP + 3):
            process.remember(st, [i], "/p", 0, 1)
        self.assertEqual(len(st["turns"]), process.KEEP)
        self.assertNotIn("0", st["turns"])
        self.assertEqual(st["turns"][str(process.KEEP + 2)], ["/p", 0, 1, None])

    def test_long_outputs_are_cut(self):
        self.assertIn("…（還有 4900 字，對這則按 📋 接著看）", process.cut("x" * 5000, 100))
        self.assertIn("```", process.tool_line("Bash", {"command": "a```b"}).replace("'''", ""))

    def test_tool_lines_are_worded_as_they_were(self):
        """The words of each sort of tool call (stage 3b step 2.2d: which tool is of which sort is the connector's
        transcript.tool_call; the words stay here), to the letter."""
        line = process.tool_line
        self.assertEqual(line("Bash", {"command": "ls -l", "description": "List"}), "🔧 **Bash**（List）\n```\nls -l\n```")
        self.assertEqual(line("Bash", {"command": "ls"}), "🔧 **Bash**\n```\nls\n```")
        self.assertEqual(line("Bash", None), "🔧 **Bash**\n```\n\n```")
        self.assertEqual(line("Read", {"file_path": "/a"}), "🔧 **Read** `/a`")
        self.assertEqual(line("Read", {}), "🔧 **Read** `?`")
        self.assertEqual(line("Edit", {"file_path": "/a", "old_string": "abc", "new_string": "de"}), "🔧 **Edit** `/a`（改 3 字 → 2 字）")
        self.assertEqual(line("Edit", {"file_path": "/a"}), "🔧 **Edit** `/a`（改 0 字 → 0 字）")
        self.assertEqual(line("Write", {"file_path": "/a", "content": "four"}), "🔧 **Write** `/a`（4 字）")
        self.assertEqual(line("NotebookEdit", {"notebook_path": "/n.ipynb", "new_source": "x"}), "🔧 **NotebookEdit** `/n.ipynb`")
        self.assertEqual(line("Grep", {"pattern": "x", "path": "/src"}), "🔧 **Grep** `x` in `/src`")
        self.assertEqual(line("Glob", {"pattern": "*.py"}), "🔧 **Glob** `*.py`")
        self.assertEqual(line("Agent", {"description": "Look", "prompt": "find"}), "🔧 **Agent**：Look")
        self.assertEqual(line("Task", {"prompt": "p" * 300}), "🔧 **Task**：" + "p" * 200 + "\n…（還有 100 字，對這則按 📋 接著看）")
        self.assertEqual(line("Agent", {}), "🔧 **Agent**：")
        self.assertEqual(line("WebFetch", {"url": "https://x", "prompt": "p"}), "🔧 **WebFetch** https://x")
        self.assertEqual(line("WebSearch", {"query": "q"}), "🔧 **WebSearch** q")
        self.assertEqual(line("mcp__x__y", {"a": "`b`"}), "🔧 **mcp__x__y** `{\"a\": \"'b'\"}`")
        self.assertEqual(line("Workflow", "broken"), "🔧 **Workflow** `{}`")

    def test_no_shape_of_a_tools_input_makes_the_line_fail(self):
        """(a 📋 that ends in silence: a Bash whose command was a number made the whole turn unreadable)"""
        odd = [None, 5, 0, 1.5, True, ["a", "b"], {"k": 1}, "text", ""]
        keys = ("command", "description", "file_path", "notebook_path", "old_string", "new_string", "content", "pattern", "path", "prompt", "url", "query")
        for name in ("Bash", "Read", "Write", "Edit", "NotebookEdit", "Grep", "Glob", "Agent", "Task", "WebFetch", "WebSearch", "Other", None, 5):
            for v in odd:
                for inp in (v, dict((k, v) for k in keys)):
                    self.assertTrue(process.tool_line(name, inp).startswith("🔧 **%s**" % name), (name, inp))
        self.assertEqual(process.tool_line("Bash", {"command": 5}), "🔧 **Bash**\n```\n5\n```")
        self.assertEqual(process.tool_line("Edit", {"file_path": "/a", "old_string": 12345, "new_string": None}), "🔧 **Edit** `/a`（改 5 字 → 0 字）")
        self.assertEqual(process.tool_line("Agent", {"prompt": 7}), "🔧 **Agent**：7")
        self.assertEqual(process.cut(5, 10), "5")
        self.assertEqual(process.cut(None, 10), "")
        self.assertEqual(process.cut("abc", 10, "many"), "abc", "a length that is no number is not gone by")

    def test_a_turn_with_broken_rows_is_shown_past_them(self):
        rows = [{"type": "user", "message": {"content": "asked it"}}, {"type": "user", "message": "not an object"}, {"type": "assistant", "message": 5},
                {"type": "assistant", "message": {"content": [{"type": "text", "text": 7}, {"type": "text", "text": "said it"}, {"type": "tool_use", "name": "Bash", "input": {"command": 5}},
                                                              {"type": "tool_use"}]}},
                {"type": "user", "message": {"content": [{"type": "tool_result", "content": [{"type": "text", "text": 4}, {"type": "text", "text": "out"}]}, {"type": "tool_result", "content": 12}]}}]
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "s.jsonl")
            with open(path, "w") as f:
                f.write("".join(json.dumps(r) + "\n" for r in rows))
            self.assertEqual(process.read_turn(path, 0, None),
                             ["🧑 **你**\nasked it", "💬 said it", "🔧 **Bash**\n```\n5\n```", "🔧 **?** `{}`", "```\nout\n```", "```\n12\n```"])


if __name__ == "__main__":
    unittest.main()
