"""!resume, the recap and the history of a conversation as the owner reads them (bot/sessions.py), and dcctl history. Where
Claude Code keeps conversations and how they are read is the connector's (its sessions.py, tested beside it since stage 3b
step 1.7); here the words, the lists, and what a channel remembers."""
import importlib.machinery
import importlib.util
import json
import os
import tempfile
import unittest

import bot  # noqa: F401  (first: it puts the engine's folder on the path; the connectors lie beside dcbot/)
from altention.connectors.claude_code import sessions as cc_sessions
from bot import sessions
from tests import tmuxguard  # noqa: F401,E402  (no test reaches a real tmux server)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def line(kind, content, **extra):
    return json.dumps(dict({"type": kind, "message": {"role": kind, "content": content}}, **extra),
                      ensure_ascii=False, separators=(",", ":")) + "\n"


def human(text):
    return line("user", text, origin={"kind": "human"})


class Sessions(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old = os.environ.get("CLAUDE_CONFIG_DIR")
        os.environ["CLAUDE_CONFIG_DIR"] = self.tmp.name
        self.dir = cc_sessions.project_dir("/work/my.proj")
        os.makedirs(self.dir)

    def tearDown(self):
        if self.old is None:
            del os.environ["CLAUDE_CONFIG_DIR"]
        else:
            os.environ["CLAUDE_CONFIG_DIR"] = self.old
        self.tmp.cleanup()

    def write(self, sid, text, mtime):
        path = os.path.join(self.dir, sid + ".jsonl")
        with open(path, "w") as f:
            f.write(text)
        os.utime(path, (mtime, mtime))
        return path

    def test_pick_and_listing(self):
        self.write("aaaa1111-x", human("old one"), 100)
        self.write("bbbb2222-y", human("new one") + human("its last"), 200)
        self.write("cccc3333-z", line("assistant", [{"type": "text", "text": "nobody typed here"}]), 300)
        items = cc_sessions.recent("/work/my.proj")
        self.assertEqual([s["id"] for s in items], ["bbbb2222-y", "aaaa1111-x"])
        self.assertEqual(sessions.pick(items, "2")["id"], "aaaa1111-x")
        self.assertEqual(sessions.pick(items, "bbbb")["id"], "bbbb2222-y")
        self.assertIsNone(sessions.pick(items, "3"))
        self.assertIsNone(sessions.pick(items, "bb"))
        text = sessions.listing(items, current="bbbb2222-y", mine=["aaaa1111-x"])
        self.assertIn("`bbbb2222`　← 目前", text)
        self.assertIn("`aaaa1111`　★", text)
        self.assertIn("its last", text)
        self.assertIn("其他頻道使用中", sessions.listing(items, taken=["aaaa1111-x"]))
        self.assertIn("還沒有", sessions.listing(cc_sessions.recent("/nowhere")))

    def test_remember_keeps_order_without_duplicates(self):
        st = {}
        for sid in ("a", "b", None, "a"):
            sessions.remember(st, sid)
        self.assertEqual(st["history"], ["b", "a"])


class History(unittest.TestCase):
    def test_format_message(self):
        loader = importlib.machinery.SourceFileLoader("dcctl", os.path.join(ROOT, "tools", "dcctl"))
        dcctl = importlib.util.module_from_spec(importlib.util.spec_from_loader("dcctl", loader))
        loader.exec_module(dcctl)
        m = {"id": "42", "timestamp": "2026-10-02T17:00:00+00:00", "author": {"username": "owner"},
             "content": "x" * 30, "reactions": [{"emoji": {"name": "👀"}}],
             "attachments": [{"filename": "a.png", "url": "https://cdn/a.png"}]}
        out = dcctl.format_message(m, 10)
        self.assertIn("owner 👀 (id 42)", out)
        self.assertIn("x" * 10 + "…（共 30 字", out)
        self.assertIn("[附件] a.png https://cdn/a.png", out)
        self.assertIn("x" * 30, dcctl.format_message(m, 0))
        self.assertIn("機器人", dcctl.format_message({"id": "1", "timestamp": "2026-10-02T17:00:00+00:00",
                                                     "author": {"bot": True}, "content": ""}))


class Recap(unittest.TestCase):
    def test_last_exchanges_from_the_end_of_a_transcript(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "t.jsonl")
            say = lambda t: line("assistant", [{"type": "text", "text": t}])  # noqa: E731
            with open(path, "w") as f:
                f.write(human("one") + say("thinking aloud") + line("assistant", [{"type": "tool_use", "name": "Bash"}])
                        + line("user", [{"type": "tool_result", "content": "x"}]) + say("answer one")
                        + line("assistant", [{"type": "text", "text": "side"}], isSidechain=True)
                        + human("two") + say("answer two") + human("three"))
            self.assertEqual(sessions.recap(path, 2), [("two", "answer two"), ("three", "")])
            self.assertEqual(sessions.recap(path, 9)[0], ("one", "answer one"))
            self.assertEqual(sessions.recap(os.path.join(tmp, "missing")), [])
            small = sessions.recap(path, 9, tail_bytes=200)  # only the end is read; a cut first line is dropped
            self.assertEqual(small[-1], ("three", ""))
            self.assertLess(len(small), 3)

    def test_whole_history_as_messages(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "t.jsonl")
            with open(path, "w") as f:
                f.write(line("user", "first\nprompt", origin={"kind": "human"}, timestamp="2026-10-02T17:00:00.000Z")
                        + line("assistant", [{"type": "text", "text": "x" * 3000}]) + human("second"))
            msgs = sessions.history_messages(path)
            self.assertTrue(msgs[0].startswith("🧑 **"))
            self.assertIn("> first\n> prompt", msgs[0])
            self.assertEqual(sum(m.count("x") for m in msgs), 3000, "nothing is cut")
            self.assertTrue(all(len(m) <= 2000 for m in msgs))
            self.assertEqual(msgs[-1], "（這一輪沒有文字回覆）")
            self.assertEqual(sessions.recap(path, None, None, stamps=True)[0][2], "2026-10-02T17:00:00.000Z")

    def test_background_tasks_and_queued_messages_open_turns_too(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "t.jsonl")
            note = "<task-notification>\n<task-id>T1</task-id>\n<status>completed</status>\n<summary>Run B1b finished</summary>\n</task-notification>"
            att = lambda prompt, mode: json.dumps({"type": "attachment", "attachment": {"type": "queued_command", "prompt": prompt,  # noqa: E731
                                                                                         "commandMode": mode}}) + "\n"
            with open(path, "w") as f:
                f.write(human("start the run") + line("assistant", [{"type": "text", "text": "Started."}])
                        + att(note, "task-notification") + line("assistant", [{"type": "text", "text": "B1b: loss 0.12"}])
                        + line("user", note, origin={"kind": "task-notification"})  # the same one, delivered: not twice
                        + att("also log it", "prompt") + line("assistant", [{"type": "text", "text": "Logged."}]))
            pairs = sessions.recap(path, None, None)
            self.assertEqual(pairs, [("start the run", "Started."), ("⚙️ 背景工作完成：Run B1b finished", "B1b: loss 0.12"),
                                     ("（處理中途補充）also log it", "Logged.")])
            msgs = sessions.history_messages(path)
            self.assertTrue(any(m.startswith("⚙️ **") and "Run B1b finished" in m for m in msgs))
            self.assertIn("B1b: loss 0.12", msgs)

    def test_recap_text(self):
        text = sessions.recap_text([("line a\nline b", "reply " + "x" * 3000), ("q", "")], 400, 50)
        self.assertIn("> line a\n> line b\n", text)
        self.assertIn("…（略）", text)
        self.assertIn("> q\n（這一輪還沒有回覆）", text)
        self.assertIn("讀不到", sessions.recap_text([]))

    def test_the_words_for_what_opened_a_turn(self):
        self.assertEqual(sessions.worded("asked", "typed"), "typed")
        self.assertEqual(sessions.worded("midway", "also log it"), "（處理中途補充）also log it")
        self.assertEqual(sessions.worded("task", "Run B1b finished"), "⚙️ 背景工作完成：Run B1b finished")
        self.assertEqual(sessions.worded("task", None), "⚙️ 背景工作完成：（沒有說明）")
        self.assertEqual(sessions.worded("task", "x" * 400), "⚙️ 背景工作完成：" + "x" * 300, "a long summary is cut here, not in the connector")

    def test_a_conversation_read_on_another_machine_reads_the_same(self):
        """tools/dcremote backfill sends the turns as the connector gives them (plain data, through JSON); the bot words
        them with the same function as for a transcript of its own machine."""
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "t.jsonl")
            note = "<task-notification>\n<task-id>T1</task-id>\n<summary>Run B1b finished</summary>\n</task-notification>"
            with open(path, "w") as f:
                f.write(line("user", "first\nprompt", origin={"kind": "human"}, timestamp="2026-10-02T17:00:00.000Z")
                        + line("assistant", [{"type": "text", "text": "x" * 3000}])
                        + line("user", note, origin={"kind": "task-notification"}, timestamp="2026-10-02T18:00:00.000Z")
                        + line("assistant", [{"type": "text", "text": "B1b: loss 0.12"}]) + human("second"))
            sent = json.loads(json.dumps(cc_sessions.turns(path, None, None)))
            self.assertEqual(sessions.messages_of(sent), sessions.history_messages(path))
            self.assertEqual(sessions.messages_of([]), [])
            self.assertEqual(sessions.backfilled({"session_id": "s", "turns": sent}), sessions.history_messages(path))
            self.assertEqual(sessions.backfilled({"session_id": "s", "messages": ["made over there"]}), [],
                             "the ready-made messages of stage 3b step 1.7 are no longer read (nor sent)")
            self.assertEqual(sessions.backfilled({"session_id": "s", "turns": []}), [])
            self.assertEqual(sessions.backfilled({}), [])


if __name__ == "__main__":
    unittest.main()
